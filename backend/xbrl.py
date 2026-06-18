"""Second fundamentals source: SEC EDGAR XBRL `companyfacts`.

yfinance (`fundamentals.py`) gives us a quick income statement, but the
authoritative numbers live in each filer's XBRL data at SEC EDGAR. This module
pulls https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json, maps the
us-gaap concepts onto the same canonical vocabulary the rest of the pipeline
uses, and writes them into the shared `fundamentals` table tagged
`source='edgar_xbrl'`. `metrics.py` already prioritizes `edgar_xbrl` over
`yfinance`, so once XBRL rows land they transparently supersede yfinance for
the same fiscal period.

Period-end alignment: yfinance normalizes fiscal-year ends to the month end
(NVDA reports 2026-01-31 for a FY that actually ended ~Jan 25). To make the two
sources dedupe cleanly we snap each XBRL end-date to the nearest existing
yfinance `period_end` for that ticker (within a few weeks); absent a match we
normalize to the end of the month so future yfinance pulls line up.

Non-SEC filers (Samsung 005930, SK hynix 000660) have no CIK and are skipped.
Foreign private issuers (TSM, ASML, UMC, GFS, ARM, NBIS) file 20-F and report
in their statement currency — the row's `unit` carries that currency.

CLI:
    python -m backend.xbrl refresh NVDA
    python -m backend.xbrl refresh "Foundry & Manufacturing"
    python -m backend.xbrl refresh all
    python -m backend.xbrl show NVDA           # print stored edgar_xbrl facts
    python -m backend.xbrl compare NVDA        # XBRL vs yfinance, side by side
"""
from __future__ import annotations

import calendar
import sys
from datetime import date, datetime, timezone

from backend.companies import BENCHMARK_TICKERS, COMPANIES, SEGMENTS
from backend.db import connect, init_schema
from backend.edgar import _http_get, cik10, get_ticker_cik_map

# canonical concept -> us-gaap tags in preference order (first present wins).
XBRL_CONCEPTS: dict[str, list[str]] = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "NetIncome":       ["NetIncomeLoss", "ProfitLoss"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "GrossProfit":     ["GrossProfit"],
    "DilutedEPS":      ["EarningsPerShareDiluted"],
    "BasicEPS":        ["EarningsPerShareBasic"],
}

# Foreign private issuers (TSM, UMC, GFS) file 20-F under the IFRS taxonomy, not
# us-gaap. Map the same canonical concepts onto ifrs-full tags. Values arrive in
# the statement currency (TSM/UMC in TWD, GFS in USD) — `_pick_unit` tags them.
IFRS_CONCEPTS: dict[str, list[str]] = {
    "Revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "NetIncome": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "OperatingIncome": ["ProfitLossFromOperatingActivities"],
    "GrossProfit": ["GrossProfit"],
    "DilutedEPS": ["DilutedEarningsLossPerShare"],
    "BasicEPS": ["BasicEarningsLossPerShare"],
}

# us-gaap tags whose values are per-share (units like 'USD/shares').
_PER_SHARE = {"DilutedEPS", "BasicEPS"}

# Annual report forms (10-K domestic, 20-F/40-F foreign private issuers).
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# An annual income-statement period spans ~1 year. Allow a 52/53-week slack.
_MIN_DAYS, _MAX_DAYS = 340, 380

# Snap an XBRL end-date to a yfinance period_end within this many days.
_SNAP_DAYS = 25


def _segment_for(ticker: str) -> str:
    for c in COMPANIES:
        if c[0] == ticker:
            return c[3]
    return ""


def _existing_period_ends(ticker: str) -> list[date]:
    """yfinance (or any prior) period_ends already stored, for snap alignment."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT period_end FROM fundamentals "
            "WHERE ticker=? AND period_type='FY'",
            (ticker,),
        ).fetchall()
    out: list[date] = []
    for (p,) in rows:
        try:
            out.append(date.fromisoformat(p))
        except (ValueError, TypeError):
            pass
    return out


def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def _normalize_end(raw_end: date, snap_targets: list[date]) -> str:
    """Align an XBRL fiscal end-date to the stored convention.

    Prefer the nearest existing yfinance period_end (so the two sources collapse
    onto one row); otherwise normalize to month-end like yfinance does.
    """
    best: date | None = None
    best_gap = _SNAP_DAYS + 1
    for t in snap_targets:
        gap = abs((t - raw_end).days)
        if gap < best_gap:
            best_gap, best = gap, t
    if best is not None and best_gap <= _SNAP_DAYS:
        return best.isoformat()
    return _month_end(raw_end).isoformat()


def _preferred_currency(ticker: str) -> str:
    """Statement currency already stored (from yfinance) — so XBRL lands in the
    same units and supersedes cleanly. companyfacts often carries a convenience
    USD translation alongside the functional currency (TSM/UMC in TWD); matching
    the existing currency avoids a spurious source mismatch. Defaults to USD.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT unit FROM fundamentals WHERE ticker=? AND concept='Revenue' "
            "AND unit IS NOT NULL ORDER BY period_end DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    return (row[0] if row and row[0] else "USD")


def _pick_unit(
    units: dict[str, list], per_share: bool, pref_currency: str = "USD",
) -> tuple[str, list] | None:
    """Choose the reporting unit. Prefer the filer's statement currency, then USD,
    then the first currency present. Per-share units look like 'USD/shares'.
    """
    if not units:
        return None
    keys = list(units.keys())
    order = [pref_currency, "USD"]
    for cur in order:
        want = f"{cur}/shares" if per_share else cur
        if want in units:
            return want, units[want]
    for k in keys:  # any per-share variant, else any key
        if per_share and k.endswith("/shares"):
            return k, units[k]
    return keys[0], units[keys[0]]


def _annual_facts(entries: list[dict]) -> dict[date, tuple[float, str]]:
    """From a concept's unit entries, keep annual figures keyed by raw end-date.

    On duplicate end-dates (original 10-K then a later amendment/restatement) the
    most recently *filed* value wins.
    """
    out: dict[date, tuple[float, str]] = {}  # end -> (value, filed)
    for e in entries:
        form = (e.get("form") or "").upper()
        if form not in _ANNUAL_FORMS:
            continue
        start_s, end_s, val = e.get("start"), e.get("end"), e.get("val")
        if not end_s or val is None:
            continue
        try:
            end_d = date.fromisoformat(end_s)
        except ValueError:
            continue
        if start_s:  # duration fact — enforce ~1-year span
            try:
                span = (end_d - date.fromisoformat(start_s)).days
            except ValueError:
                continue
            if not (_MIN_DAYS <= span <= _MAX_DAYS):
                continue
        filed = e.get("filed") or ""
        cur = out.get(end_d)
        if cur is None or filed >= cur[1]:
            out[end_d] = (float(val), filed)
    return out


def fetch_xbrl_for(ticker: str, cik_map: dict[str, int] | None = None) -> int:
    """Pull annual XBRL facts for one ticker into `fundamentals`. Return rows written."""
    cik_map = cik_map if cik_map is not None else get_ticker_cik_map()
    cik = cik_map.get(ticker.upper())
    if cik is None:
        print(f"  {ticker}: no SEC CIK (non-SEC filer) — skipped")
        return 0

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10(cik)}.json"
    try:
        data = _http_get(url).json()
    except Exception as exc:  # noqa: BLE001
        print(f"  {ticker}: companyfacts fetch failed: {exc}")
        return 0

    facts = data.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    ifrs = facts.get("ifrs-full", {})
    if not us_gaap and not ifrs:
        print(f"  {ticker}: no us-gaap or ifrs-full facts")
        return 0
    # Prefer whichever taxonomy the filer actually uses (domestic=us-gaap,
    # foreign private issuer=ifrs-full). For each concept, try us-gaap tags first
    # then ifrs-full tags so a mixed filer still resolves.
    candidates: dict[str, list[tuple[dict, str]]] = {}
    for concept, tags in XBRL_CONCEPTS.items():
        candidates.setdefault(concept, []).extend((us_gaap, t) for t in tags)
    for concept, tags in IFRS_CONCEPTS.items():
        candidates.setdefault(concept, []).extend((ifrs, t) for t in tags)

    snap_targets = _existing_period_ends(ticker)
    pref_currency = _preferred_currency(ticker)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[tuple] = []

    for concept, ns_tags in candidates.items():
        per_share = concept in _PER_SHARE
        # A concept's history can be split across tags/taxonomies by era (NVDA
        # reports revenue under both 'Revenues' and 'RevenueFromContractWith...').
        # Union the coverage: iterate in preference order, earlier tags win ties.
        merged: dict[date, tuple[float, str, str]] = {}  # end -> (value, unit, tag)
        for ns, tag in ns_tags:
            node = ns.get(tag)
            if node is None:
                continue
            picked = _pick_unit(node.get("units", {}), per_share, pref_currency)
            if picked is None:
                continue
            unit_key, entries = picked
            unit = unit_key.replace("/shares", "/share")  # match yfinance label
            for raw_end, (value, _filed) in _annual_facts(entries).items():
                if raw_end not in merged:
                    merged[raw_end] = (value, unit, tag)
        for raw_end, (value, unit, tag) in merged.items():
            period_end = _normalize_end(raw_end, snap_targets)
            rows.append((
                ticker.upper(), period_end, "FY", concept, value, unit,
                "edgar_xbrl", f"edgar:companyfacts:{tag}", fetched_at,
            ))

    if not rows:
        print(f"  {ticker}: no annual XBRL concepts matched")
        return 0

    init_schema()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO fundamentals "
            "(ticker, period_end, period_type, concept, value, unit, source, source_ref, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, period_end, period_type, concept, source) "
            "DO UPDATE SET value=excluded.value, unit=excluded.unit, "
            "    source_ref=excluded.source_ref, fetched_at=excluded.fetched_at",
            rows,
        )
        conn.commit()
    return len(rows)


def _resolve_segment(scope: str) -> list[str] | None:
    if scope == "all":
        return None
    if scope in set(SEGMENTS):
        return [c[0] for c in COMPANIES if c[3] == scope and c[0] not in BENCHMARK_TICKERS]
    return [scope]


def refresh(filter_tickers: list[str] | None = None) -> None:
    init_schema()
    if filter_tickers:
        wanted = {t.upper() for t in filter_tickers}
        targets = [c[0] for c in COMPANIES if c[0] in wanted and c[0] not in BENCHMARK_TICKERS]
    else:
        targets = [c[0] for c in COMPANIES if c[0] not in BENCHMARK_TICKERS]

    cik_map = get_ticker_cik_map()
    print(f"Fetching XBRL companyfacts for {len(targets)} ticker(s)...")
    inserted = 0
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for i, t in enumerate(targets, 1):
        try:
            n = fetch_xbrl_for(t, cik_map)
            inserted += n
            if n == 0:
                skipped.append(t)
            else:
                print(f"  [{i:>2}/{len(targets)}] {t:<7} {n:>3} rows")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:>2}/{len(targets)}] {t:<7} FAILED: {exc}")
            failed.append((t, str(exc)))
    print(f"\nDone. {inserted} rows inserted/updated.")
    if skipped:
        print(f"Skipped (no SEC XBRL): {', '.join(skipped)}")
    if failed:
        print(f"\n{len(failed)} failures:")
        for t, e in failed:
            print(f"  {t}: {e}")


def show(ticker: str) -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT period_end, concept, value, unit, source_ref FROM fundamentals "
            "WHERE ticker=? AND source='edgar_xbrl' ORDER BY concept, period_end DESC",
            (ticker.upper(),),
        ).fetchall()
    if not rows:
        print(f"No edgar_xbrl fundamentals for {ticker}.")
        return
    print(f"{'PERIOD':<12}{'CONCEPT':<18}{'VALUE':>20}  UNIT")
    for r in rows:
        v = r[2]
        vfmt = f"{v:>20,.2f}" if isinstance(v, float) else str(v)
        print(f"{r[0]:<12}{r[1]:<18}{vfmt}  {r[3] or ''}")


def compare(ticker: str) -> None:
    """Side-by-side XBRL vs yfinance for matching (period_end, concept)."""
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT period_end, concept, source, value, unit FROM fundamentals "
            "WHERE ticker=? AND source IN ('edgar_xbrl','yfinance') "
            "ORDER BY concept, period_end DESC",
            (ticker.upper(),),
        ).fetchall()
    pairs: dict[tuple[str, str], dict[str, tuple[float, str]]] = {}
    for period_end, concept, source, value, unit in rows:
        pairs.setdefault((concept, period_end), {})[source] = (value, unit)
    if not pairs:
        print(f"No fundamentals for {ticker}.")
        return
    hdr = f"{'CONCEPT':<16}{'PERIOD':<12}{'XBRL':>18}{'YFINANCE':>18}  DELTA"
    print(hdr)
    for (concept, period_end) in sorted(pairs, key=lambda k: (k[0], k[1]), reverse=True):
        d = pairs[(concept, period_end)]
        x = d.get("edgar_xbrl")
        y = d.get("yfinance")
        xs = f"{x[0]:>18,.2f}" if x else f"{'—':>18}"
        ys = f"{y[0]:>18,.2f}" if y else f"{'—':>18}"
        delta = ""
        if x and y and y[0]:
            pct = (x[0] - y[0]) / abs(y[0]) * 100.0
            delta = f"{pct:+.3f}%"
        print(f"{concept:<16}{period_end:<12}{xs}{ys}  {delta}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            '  python -m backend.xbrl refresh <TICKER|all|"<segment>">\n'
            "  python -m backend.xbrl show <TICKER>\n"
            "  python -m backend.xbrl compare <TICKER>"
        )
        return
    cmd = args[0]
    if cmd == "refresh":
        if len(args) < 2:
            raise SystemExit("refresh needs a target")
        refresh(_resolve_segment(args[1]))
    elif cmd == "show":
        if len(args) < 2:
            raise SystemExit("show needs a TICKER")
        show(args[1])
    elif cmd == "compare":
        if len(args) < 2:
            raise SystemExit("compare needs a TICKER")
        compare(args[1])
    else:
        raise SystemExit(f"Unknown: {cmd!r}")


if __name__ == "__main__":
    main()
