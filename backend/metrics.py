"""Stage 2 of the fundamentals pipeline: derive panel-ready metrics from
the raw `fundamentals` table.

Computes per ticker:
- revenue_latest + revenue_latest_period
- operating_margin (Operating Income / Revenue; falls back to Net Income / Revenue)
- gross_margin (Gross Profit / Revenue) — a key lens for semis
- revenue_3y_cagr (with window like 'FY2022 -> FY2025')
- eps_3y_cagr (with caveat if any value in the window is non-positive)

Writes to `company_metrics` (one row per ticker).

CLI:
    python -m backend.metrics refresh NVDA
    python -m backend.metrics refresh "Foundry & Manufacturing"
    python -m backend.metrics refresh all
    python -m backend.metrics show NVDA
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from backend.companies import BENCHMARK_TICKERS, COMPANIES
from backend.db import connect, init_schema


def _segment_for(ticker: str) -> str:
    for c in COMPANIES:
        if c[0] == ticker:
            return c[3]
    return ""


def _facts_by_concept(ticker: str) -> dict[str, list[tuple[str, float]]]:
    """Return {concept: [(period_end, value), ...] sorted desc by period_end}.
    Source priority: edgar_xbrl > yfinance > manual (only one normally exists today).
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT period_end, concept, value, source FROM fundamentals "
            "WHERE ticker=? AND period_type='FY' AND value IS NOT NULL "
            "ORDER BY period_end DESC",
            (ticker,),
        ).fetchall()
    by_concept: dict[str, dict[str, tuple[float, str]]] = {}
    PRIORITY = {"edgar_xbrl": 3, "yfinance": 2, "manual": 1}
    for period_end, concept, value, source in rows:
        slot = by_concept.setdefault(concept, {})
        cur = slot.get(period_end)
        if cur is None or PRIORITY.get(source, 0) > PRIORITY.get(cur[1], 0):
            slot[period_end] = (float(value), source)
    out: dict[str, list[tuple[str, float]]] = {}
    for concept, periods in by_concept.items():
        out[concept] = sorted([(p, v[0]) for p, v in periods.items()], reverse=True)
    return out


def _financial_currency(ticker: str) -> str:
    """Reporting currency of the monetary facts (from the Revenue row's unit)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT unit FROM fundamentals "
            "WHERE ticker=? AND concept='Revenue' AND unit IS NOT NULL "
            "ORDER BY period_end DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return (row[0] if row and row[0] else "USD")


def _fy_label(period_end: str) -> str:
    # period_end is YYYY-MM-DD; yfinance reports the period end, so FY = ending year.
    return "FY" + period_end[:4]


def _cagr(start_value: float, end_value: float, years: int) -> float | None:
    if start_value is None or end_value is None or years <= 0:
        return None
    if start_value <= 0 or end_value <= 0:
        return None  # CAGR undefined when crossing zero or negative
    return (end_value / start_value) ** (1.0 / years) - 1.0


def compute_metrics_for(ticker: str) -> dict:
    """Return the metrics dict for one ticker (also writes to DB)."""
    facts = _facts_by_concept(ticker)
    rev = facts.get("Revenue", [])
    ni = facts.get("NetIncome", [])
    op = facts.get("OperatingIncome", [])
    gp = facts.get("GrossProfit", [])
    eps_d = facts.get("DilutedEPS", [])
    eps_b = facts.get("BasicEPS", [])
    eps = eps_d if eps_d else eps_b

    metrics: dict = {
        "ticker": ticker,
        "revenue_latest": None, "revenue_latest_period": None,
        "financial_currency": _financial_currency(ticker),
        "operating_margin": None, "operating_margin_basis": None,
        "gross_margin": None,
        "revenue_3y_cagr": None, "revenue_cagr_window": None,
        "eps_3y_cagr": None, "eps_cagr_window": None, "eps_cagr_caveat": None,
        "sources": "yfinance",
        "last_updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    # ---- revenue + revenue CAGR ----
    if rev:
        latest_period, latest_rev = rev[0]
        metrics["revenue_latest"] = latest_rev
        metrics["revenue_latest_period"] = _fy_label(latest_period)
        if len(rev) >= 4:
            old_period, old_rev = rev[3]
            cagr = _cagr(old_rev, latest_rev, 3)
            if cagr is not None:
                metrics["revenue_3y_cagr"] = cagr
                metrics["revenue_cagr_window"] = f"{_fy_label(old_period)} → {_fy_label(latest_period)}"
        elif len(rev) >= 2:
            old_period, old_rev = rev[-1]
            n = int(latest_period[:4]) - int(old_period[:4])
            if n > 0:
                cagr = _cagr(old_rev, latest_rev, n)
                if cagr is not None:
                    metrics["revenue_3y_cagr"] = cagr
                    metrics["revenue_cagr_window"] = f"{_fy_label(old_period)} → {_fy_label(latest_period)} ({n}Y)"

    # ---- margins (latest period) ----
    if rev:
        latest_period, latest_rev = rev[0]
        if latest_rev > 0:
            op_match = next((v for p, v in op if p == latest_period), None)
            if op_match is not None:
                metrics["operating_margin"] = op_match / latest_rev
                metrics["operating_margin_basis"] = "OperatingIncome/Revenue"
            else:
                ni_match = next((v for p, v in ni if p == latest_period), None)
                if ni_match is not None:
                    metrics["operating_margin"] = ni_match / latest_rev
                    metrics["operating_margin_basis"] = "NetIncome/Revenue (fallback)"

            gp_match = next((v for p, v in gp if p == latest_period), None)
            if gp_match is not None:
                metrics["gross_margin"] = gp_match / latest_rev

    # ---- EPS CAGR ----
    if eps and len(eps) >= 4:
        old_period, old_eps = eps[3]
        latest_period, latest_eps = eps[0]
        cagr = _cagr(old_eps, latest_eps, 3)
        if cagr is not None:
            metrics["eps_3y_cagr"] = cagr
            metrics["eps_cagr_window"] = f"{_fy_label(old_period)} → {_fy_label(latest_period)}"
        else:
            losses = [p for p, v in eps[:4] if v <= 0]
            if losses:
                metrics["eps_cagr_caveat"] = (
                    f"CAGR not meaningful — non-positive EPS in {_fy_label(losses[-1])} "
                    f"makes compound growth from a non-positive base undefined."
                )

    # ---- write ----
    init_schema()
    with connect() as conn:
        conn.execute(
            "INSERT INTO company_metrics(ticker, revenue_latest, revenue_latest_period, "
            "financial_currency, operating_margin, operating_margin_basis, gross_margin, "
            "revenue_3y_cagr, revenue_cagr_window, eps_3y_cagr, eps_cagr_window, eps_cagr_caveat, "
            "sources, last_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "revenue_latest=excluded.revenue_latest, revenue_latest_period=excluded.revenue_latest_period, "
            "financial_currency=excluded.financial_currency, operating_margin=excluded.operating_margin, "
            "operating_margin_basis=excluded.operating_margin_basis, gross_margin=excluded.gross_margin, "
            "revenue_3y_cagr=excluded.revenue_3y_cagr, revenue_cagr_window=excluded.revenue_cagr_window, "
            "eps_3y_cagr=excluded.eps_3y_cagr, eps_cagr_window=excluded.eps_cagr_window, "
            "eps_cagr_caveat=excluded.eps_cagr_caveat, sources=excluded.sources, "
            "last_updated_at=excluded.last_updated_at",
            (ticker, metrics["revenue_latest"], metrics["revenue_latest_period"],
             metrics["financial_currency"], metrics["operating_margin"], metrics["operating_margin_basis"],
             metrics["gross_margin"], metrics["revenue_3y_cagr"], metrics["revenue_cagr_window"],
             metrics["eps_3y_cagr"], metrics["eps_cagr_window"], metrics["eps_cagr_caveat"],
             metrics["sources"], metrics["last_updated_at"]),
        )
        conn.commit()
    return metrics


def get_metrics(ticker: str) -> dict | None:
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT ticker, revenue_latest, revenue_latest_period, financial_currency, "
            "operating_margin, operating_margin_basis, gross_margin, revenue_3y_cagr, "
            "revenue_cagr_window, eps_3y_cagr, eps_cagr_window, eps_cagr_caveat, sources, "
            "last_updated_at FROM company_metrics WHERE ticker=?",
            (ticker,),
        ).fetchone()
    if not row:
        return None
    return {
        "ticker": row[0],
        "revenue_latest": row[1], "revenue_latest_period": row[2],
        "financial_currency": row[3],
        "operating_margin": row[4], "operating_margin_basis": row[5],
        "gross_margin": row[6],
        "revenue_3y_cagr": row[7], "revenue_cagr_window": row[8],
        "eps_3y_cagr": row[9], "eps_cagr_window": row[10], "eps_cagr_caveat": row[11],
        "sources": row[12], "last_updated_at": row[13],
    }


def refresh(filter_tickers: list[str] | None = None) -> None:
    if filter_tickers:
        wanted = {t.upper() for t in filter_tickers}
        targets = [c[0] for c in COMPANIES if c[0] in wanted and c[0] not in BENCHMARK_TICKERS]
    else:
        targets = [c[0] for c in COMPANIES if c[0] not in BENCHMARK_TICKERS]
    print(f"Computing metrics for {len(targets)} ticker(s)...")
    for i, t in enumerate(targets, 1):
        m = compute_metrics_for(t)
        rev = (m["revenue_latest"] / 1e9) if m["revenue_latest"] else None
        gm = (m["gross_margin"] * 100) if m["gross_margin"] is not None else None
        op_m = (m["operating_margin"] * 100) if m["operating_margin"] is not None else None
        rcagr = (m["revenue_3y_cagr"] * 100) if m["revenue_3y_cagr"] is not None else None
        revs = f"{rev:.1f}B" if rev is not None else "—"
        gms = f"{gm:.0f}%" if gm is not None else "—"
        opms = f"{op_m:.0f}%" if op_m is not None else "—"
        rcs = f"{rcagr:.0f}%" if rcagr is not None else "—"
        print(f"  [{i:>2}/{len(targets)}] {t:<7} rev={revs:>8}  gm={gms:>5}  opm={opms:>5}  rev_cagr={rcs:>5}")
    print("Done.")


def show(ticker: str) -> None:
    m = get_metrics(ticker.upper())
    if not m:
        print(f"No metrics for {ticker}")
        return
    import json
    print(json.dumps(m, indent=2))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            '  python -m backend.metrics refresh <TICKER|all|"<segment>">\n'
            "  python -m backend.metrics show <TICKER>"
        )
        return
    cmd = args[0]
    if cmd == "refresh":
        if len(args) < 2:
            raise SystemExit("refresh needs a target")
        from backend.fundamentals import _resolve_segment
        refresh(_resolve_segment(args[1]))
    elif cmd == "show":
        if len(args) < 2:
            raise SystemExit("show needs a TICKER")
        show(args[1])
    else:
        raise SystemExit(f"Unknown: {cmd!r}")


if __name__ == "__main__":
    main()
