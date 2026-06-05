"""FastAPI app exposing the AI-sector heat-map data + live snapshots.

Run:
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.companies import BENCHMARK_TICKERS, PRIMARY_BENCHMARK, SEGMENTS
from backend.db import connect, get_meta
from backend.returns import HORIZONS_YEARS, compute_table

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="AI Sector Investment Platform")

# Local-only app — wide-open CORS so file:// / preview origins also work.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],   # GET for reads; POST/DELETE for watchlist admin
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/companies")
def companies() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, name, segment, layer, notes FROM companies "
            "ORDER BY layer, ticker"
        ).fetchall()
    return {
        "companies": [
            {"ticker": t, "name": n, "segment": s, "layer": l, "notes": notes}
            for (t, n, s, l, notes) in rows
        ]
    }


@app.get("/api/returns")
def returns() -> dict[str, Any]:
    df = compute_table()
    df = df.where(pd.notna(df), None)  # NaN -> None for JSON
    as_of_dates = [d for d in df["as_of"].dropna().tolist()]
    as_of = max(as_of_dates) if as_of_dates else None

    horizons = [f"{y}y" for y in HORIZONS_YEARS]

    def to_record(r) -> dict[str, Any]:
        return {
            "ticker": r["ticker"],
            "name": r["name"],
            "segment": r["segment"],
            "layer": int(r["layer"]) if r["layer"] is not None else 99,
            "as_of": r["as_of"],
            "returns": {h: (None if r[h] is None else float(r[h])) for h in horizons},
        }

    benchmarks: list[dict[str, Any]] = []
    primary: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        rec = to_record(r)
        if r["ticker"] in BENCHMARK_TICKERS:
            benchmarks.append(rec)
            if r["ticker"] == PRIMARY_BENCHMARK:
                primary = rec
        else:
            records.append(rec)

    return {
        "as_of": as_of,
        "last_refresh_at": get_meta("last_refresh_at"),
        "horizons": horizons,
        "benchmarks": benchmarks,
        "primary_benchmark": primary,
        "data": records,
    }


def _company_meta(ticker: str) -> tuple[str, str, str] | None:
    """(yahoo_ticker, name, segment) for a display ticker, or None.

    Reads the live `companies` table (not the seed list) so companies added at
    runtime via the watchlist admin also resolve for snapshots.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT yahoo_ticker, name, segment FROM companies WHERE ticker=?",
            (ticker.upper(),),
        ).fetchone()
    return (row[0], row[1], row[2]) if row else None


@app.get("/api/snapshot/{ticker}")
def snapshot(ticker: str) -> dict[str, Any]:
    """Live(ish) snapshot for the side panel / stock page hero strip.

    Prices come from the local DB; market_cap / P/E / dividend_yield come from a
    best-effort yfinance call (None on failure). Foreign rows (e.g. 005930) are
    priced in their local currency — `currency` reports which.
    """
    t = ticker.upper()
    meta = _company_meta(t)
    if meta is None:
        return {"ticker": t, "exists": False}
    yahoo_ticker, name, segment = meta

    with connect() as conn:
        rows = conn.execute(
            "SELECT date, adj_close FROM prices WHERE ticker=? ORDER BY date",
            (t,),
        ).fetchall()
    if not rows:
        return {"ticker": t, "exists": False}

    # 5Y sparkline: last ~252*5 trading days, downsampled to ~120 points
    five_y = rows[-1260:] if len(rows) > 1260 else rows
    step = max(1, len(five_y) // 120)
    sparkline = [{"date": d, "close": c} for (d, c) in five_y[::step]]

    latest_close = float(rows[-1][1])
    prev_close = float(rows[-2][1]) if len(rows) >= 2 else None
    day_change_pct = (latest_close / prev_close - 1.0) if prev_close else None

    market_cap = pe_ratio = dividend_yield = currency = None
    try:
        import yfinance as yf
        from backend.etl import _SESSION
        tk = yf.Ticker(yahoo_ticker, session=_SESSION) if _SESSION else yf.Ticker(yahoo_ticker)
        fast = tk.fast_info
        market_cap = float(fast["marketCap"]) if fast.get("marketCap") else None
        currency = fast.get("currency")
        try:
            full = tk.info
            pe_ratio = full.get("trailingPE") or full.get("forwardPE")
            # yfinance >=0.2.5x returns dividendYield in PERCENT units
            # (e.g. 1.86 = 1.86%). Store as a fraction for the frontend.
            dividend_yield = full.get("dividendYield")
            if dividend_yield is not None:
                dividend_yield = dividend_yield / 100.0
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

    return {
        "ticker": t,
        "name": name,
        "segment": segment,
        "exists": True,
        "latest_close": latest_close,
        "prev_close": prev_close,
        "day_change_pct": day_change_pct,
        "as_of_date": rows[-1][0],
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "dividend_yield": dividend_yield,
        "currency": currency,
        "sparkline_5y": sparkline,
    }


@app.get("/api/metrics/{ticker}")
def metrics(ticker: str) -> dict[str, Any]:
    """Derived fundamentals (revenue, margins, CAGRs) from the company_metrics table."""
    from backend.metrics import get_metrics
    t = ticker.upper()
    m = get_metrics(t)
    if m is None:
        return {"ticker": t, "exists": False}
    return {"exists": True, **m}


@app.get("/api/insights/stock/{ticker}")
def insight_stock(ticker: str) -> dict[str, Any]:
    """Latest LLM-generated stock panel, or exists=False if none stored yet."""
    from backend.insights import get_latest
    t = ticker.upper()
    rec = get_latest("stock", t)
    if rec is None:
        return {"ticker": t, "exists": False}
    return {"exists": True, **rec}


@app.get("/api/filings/{ticker}")
def filings_for_ticker(ticker: str) -> dict[str, Any]:
    """Local SEC EDGAR filings for the ticker, newest-first.

    Domestic filers return 10-K / 10-Q / 8-K-earnings; foreign private issuers
    (TSM, ASML, UMC, GFS, ARM, NBIS) return 20-F / 6-K. Non-SEC filers
    (Samsung, SK hynix) simply have no rows.
    """
    t = ticker.upper()
    with connect() as conn:
        rows = conn.execute(
            "SELECT filing_type, form, period_end, filed_at, primary_doc_url, "
            "local_path, size_bytes FROM filings WHERE ticker=? AND status='ok' "
            "ORDER BY filed_at DESC, filing_type",
            (t,),
        ).fetchall()
    return {
        "ticker": t,
        "filings": [
            {
                "filing_type": r[0],
                "form": r[1],
                "period_end": r[2],
                "filed_at": r[3],
                "primary_doc_url": r[4],
                "local_path": r[5],
                "size_bytes": r[6],
            }
            for r in rows
        ],
    }


@app.get("/api/filing-insights/{ticker}")
def filing_insights_for_ticker(ticker: str) -> dict[str, Any]:
    """Extractive "in their own words" insights pulled verbatim from the
    company's latest annual report and earnings release on disk. No LLM —
    everything is sourced text linking back to the filing on SEC EDGAR.
    """
    from backend.filing_insights import get
    t = ticker.upper()
    rec = get(t)
    if rec is None:
        return {"ticker": t, "exists": False}
    return {"ticker": t, "exists": True, **rec}


# ---------------------------------------------------------------------------
# Watchlist administration — add / remove / re-bucket companies in the live DB.
# Mutates the `companies` table (and pulls prices on add) so the heat map can be
# curated from the UI without hand-editing backend/companies.py.
# ---------------------------------------------------------------------------


class AddCompanyBody(BaseModel):
    ticker: str
    name: str
    segment: str
    yahoo_ticker: str = ""
    notes: str = ""


class RebucketBody(BaseModel):
    segment: str


@app.get("/api/segments")
def segments() -> dict[str, Any]:
    """Value-chain segments available for bucketing (excludes 'Benchmark')."""
    return {"segments": list(SEGMENTS)}


@app.post("/api/watchlist")
def watchlist_add(body: AddCompanyBody) -> dict[str, Any]:
    """Add a company to the watchlist and pull its price history."""
    from backend.watchlist import WatchlistError, add_company
    try:
        result = add_company(
            ticker=body.ticker, name=body.name, segment=body.segment,
            yahoo_ticker=body.yahoo_ticker, notes=body.notes,
        )
    except WatchlistError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **result}


@app.patch("/api/watchlist/{ticker}")
def watchlist_rebucket(ticker: str, body: RebucketBody) -> dict[str, Any]:
    """Move a company to a different value-chain segment."""
    from backend.watchlist import WatchlistError, rebucket_company
    try:
        result = rebucket_company(ticker, body.segment)
    except WatchlistError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **result}


@app.delete("/api/watchlist/{ticker}")
def watchlist_remove(ticker: str) -> dict[str, Any]:
    """Remove a company and its derived data (prices, metrics, facts)."""
    from backend.watchlist import WatchlistError, remove_company
    try:
        result = remove_company(ticker)
    except WatchlistError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **result}


# Serve the static frontend at "/" — keep LAST so /api/* takes precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
