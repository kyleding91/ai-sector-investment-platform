"""Stage 1 of the fundamentals pipeline: fetch raw financial facts.

Source today: yfinance (most recent fiscal years of the income statement).
Source tomorrow: SEC EDGAR XBRL (authoritative; the same `fundamentals` table
would hold both, distinguished by the `source` column). Foreign rows that don't
file with the SEC (005930, 000660) will only ever have yfinance facts.

CLI:
    python -m backend.fundamentals refresh NVDA
    python -m backend.fundamentals refresh "Memory (DRAM/HBM/NAND)"   # a whole segment
    python -m backend.fundamentals refresh all
    python -m backend.fundamentals show NVDA           # print latest facts
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import pandas as pd

from backend.companies import BENCHMARK_TICKERS, COMPANIES, SEGMENTS
from backend.db import connect, init_schema
from backend.etl import _SESSION  # reuse curl_cffi browser-impersonating session

# canonical concept name  ->  list of yfinance row labels (newer + older versions)
YFINANCE_CONCEPTS: dict[str, list[str]] = {
    "Revenue":          ["Total Revenue", "TotalRevenue"],
    "NetIncome":        ["Net Income", "NetIncome", "Net Income Common Stockholders"],
    "OperatingIncome":  ["Operating Income", "OperatingIncome"],
    "GrossProfit":      ["Gross Profit", "GrossProfit"],
    "DilutedEPS":       ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"],
    "BasicEPS":         ["Basic EPS", "BasicEPS"],
}

CONCEPT_UNITS = {
    "Revenue": "USD", "NetIncome": "USD", "OperatingIncome": "USD",
    "GrossProfit": "USD", "DilutedEPS": "USD/share", "BasicEPS": "USD/share",
}


def _yahoo_ticker_for(display_ticker: str) -> str | None:
    for t, y, *_ in COMPANIES:
        if t == display_ticker:
            return y
    return None


def _pick_first_match(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """yfinance changes row names between versions. Try each candidate."""
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.index:
            return df.loc[c]
    lower_map = {str(idx).lower(): idx for idx in df.index}
    for c in candidates:
        if c.lower() in lower_map:
            return df.loc[lower_map[c.lower()]]
    return None


def fetch_yfinance_for(ticker: str) -> int:
    """Pull annual fiscal years from yfinance for one ticker. Return rows inserted."""
    yahoo = _yahoo_ticker_for(ticker)
    if yahoo is None:
        raise SystemExit(f"Unknown ticker {ticker!r}")

    import yfinance as yf
    t = yf.Ticker(yahoo, session=_SESSION) if _SESSION else yf.Ticker(yahoo)
    fin = t.financials  # annual; columns are period-end Timestamps, rows are line items

    if fin is None or fin.empty:
        print(f"  {ticker}: no annual financials returned by yfinance")
        return 0

    # Foreign filers report in local currency (TSM/UMC in TWD, Samsung/SK hynix in
    # KRW, ASML in EUR). Tag monetary facts with the statement currency so the UI
    # doesn't mislabel them as USD. Margins/CAGRs are ratios and stay currency-neutral.
    fin_currency = "USD"
    try:
        fc = t.info.get("financialCurrency")
        if fc:
            fin_currency = fc
    except Exception:  # noqa: BLE001
        pass

    rows: list[tuple] = []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for concept, candidates in YFINANCE_CONCEPTS.items():
        series = _pick_first_match(fin, candidates)
        if series is None:
            continue
        base_unit = CONCEPT_UNITS.get(concept, "USD")
        if base_unit == "USD":
            unit = fin_currency
        elif base_unit == "USD/share":
            unit = f"{fin_currency}/share"
        else:
            unit = base_unit
        for period_ts, value in series.items():
            if pd.isna(value):
                continue
            period_end = pd.Timestamp(period_ts).strftime("%Y-%m-%d")
            rows.append((
                ticker, period_end, "FY", concept, float(value), unit,
                "yfinance", "yfinance:financials", fetched_at,
            ))

    if not rows:
        print(f"  {ticker}: no concepts matched in yfinance financials")
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


def refresh(filter_tickers: list[str] | None = None) -> None:
    init_schema()
    if filter_tickers:
        wanted = {t.upper() for t in filter_tickers}
        targets = [c[0] for c in COMPANIES if c[0] in wanted and c[0] not in BENCHMARK_TICKERS]
    else:
        targets = [c[0] for c in COMPANIES if c[0] not in BENCHMARK_TICKERS]

    print(f"Fetching fundamentals for {len(targets)} ticker(s)...")
    inserted = 0
    failed: list[tuple[str, str]] = []
    for i, t in enumerate(targets, 1):
        try:
            n = fetch_yfinance_for(t)
            inserted += n
            print(f"  [{i:>2}/{len(targets)}] {t:<7} {n:>3} rows")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:>2}/{len(targets)}] {t:<7} FAILED: {exc}")
            failed.append((t, str(exc)))
        time.sleep(0.3)
    print(f"\nDone. {inserted} rows inserted/updated.")
    if failed:
        print(f"\n{len(failed)} failures:")
        for t, e in failed:
            print(f"  {t}: {e}")


def show(ticker: str) -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT period_end, period_type, concept, value, unit, source FROM fundamentals "
            "WHERE ticker=? ORDER BY concept, period_end DESC",
            (ticker.upper(),),
        ).fetchall()
    if not rows:
        print(f"No fundamentals for {ticker}.")
        return
    print(f"{'PERIOD':<12}{'TYPE':<5}{'CONCEPT':<18}{'VALUE':>20}  UNIT")
    for r in rows:
        v = r[3]
        vfmt = f"{v:>20,.2f}" if isinstance(v, float) else str(v)
        print(f"{r[0]:<12}{r[1]:<5}{r[2]:<18}{vfmt}  {r[4] or ''}")


def _resolve_segment(scope: str) -> list[str] | None:
    """Map a CLI scope to a list of tickers. 'all' -> None (everything).

    A scope can be: 'all', an exact segment name (e.g. 'Memory (DRAM/HBM/NAND)'),
    or a single ticker.
    """
    if scope == "all":
        return None
    if scope in set(SEGMENTS):
        return [c[0] for c in COMPANIES if c[3] == scope and c[0] not in BENCHMARK_TICKERS]
    return [scope]  # treat as a single ticker


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            '  python -m backend.fundamentals refresh <TICKER|all|"<segment>">\n'
            "  python -m backend.fundamentals show <TICKER>"
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
    else:
        raise SystemExit(f"Unknown: {cmd!r}")


if __name__ == "__main__":
    main()
