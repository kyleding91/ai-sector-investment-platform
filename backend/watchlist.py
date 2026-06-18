"""Runtime watchlist administration — add / remove / re-bucket companies in the DB.

The `companies` table is the runtime source of truth the heat map reads
(`returns.compute_table`, `/api/companies`). `backend/companies.py` is the seed
list; this module lets you change the *live* watchlist without hand-editing that
file (changes persist in SQLite until the next `python -m backend.seed`, which
re-applies the seed list).

Adding a company inserts the row, then pulls its price history via the ETL so it
appears on the heat map immediately. Benchmarks (SOX/SP500TR) are protected from
mutation here — they're structural.

CLI:
    python -m backend.watchlist list
    python -m backend.watchlist add QCOM QCOM "QUALCOMM Incorporated" "Analog, Power & Connectivity"
    python -m backend.watchlist rebucket QCOM "AI Accelerators / Compute"
    python -m backend.watchlist remove QCOM
"""
from __future__ import annotations

import sys

from backend.companies import BENCHMARK_TICKERS, SEGMENTS, layer_of
from backend.db import connect, init_schema

VALID_PORTFOLIO_SEGMENTS = list(SEGMENTS)  # excludes the "Benchmark" pseudo-segment


class WatchlistError(ValueError):
    """A user-correctable problem with a watchlist mutation (maps to HTTP 400)."""


def _exists(conn, ticker: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM companies WHERE ticker=?", (ticker,)
    ).fetchone() is not None


def list_companies() -> list[dict]:
    """All watchlist rows (portfolio + benchmarks), value-chain order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.ticker, c.yahoo_ticker, c.name, c.segment, c.layer, c.notes, "
            "(SELECT COUNT(*) FROM prices p WHERE p.ticker=c.ticker) AS price_rows "
            "FROM companies c ORDER BY c.layer, c.ticker"
        ).fetchall()
    return [
        {
            "ticker": r[0], "yahoo_ticker": r[1], "name": r[2], "segment": r[3],
            "layer": r[4], "notes": r[5], "price_rows": r[6],
            "is_benchmark": r[0] in BENCHMARK_TICKERS,
        }
        for r in rows
    ]


def add_company(
    ticker: str,
    name: str,
    segment: str,
    yahoo_ticker: str = "",
    notes: str = "",
    pull_prices: bool = True,
) -> dict:
    """Insert a new portfolio company and (by default) pull its price history.

    Returns {ticker, name, segment, layer, price_rows, priced}. Raises
    WatchlistError on bad input (unknown segment, duplicate, missing fields).
    """
    ticker = (ticker or "").strip().upper()
    name = (name or "").strip()
    segment = (segment or "").strip()
    yahoo_ticker = (yahoo_ticker or "").strip() or ticker
    notes = (notes or "").strip()

    if not ticker:
        raise WatchlistError("ticker is required")
    if not name:
        raise WatchlistError("name is required")
    if segment not in VALID_PORTFOLIO_SEGMENTS:
        raise WatchlistError(
            f"unknown segment {segment!r}; must be one of {VALID_PORTFOLIO_SEGMENTS}"
        )
    if ticker in BENCHMARK_TICKERS:
        raise WatchlistError(f"{ticker} is a reserved benchmark ticker")

    init_schema()
    with connect() as conn:
        if _exists(conn, ticker):
            raise WatchlistError(f"{ticker} is already on the watchlist")
        conn.execute(
            "INSERT INTO companies(ticker, yahoo_ticker, name, segment, layer, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, yahoo_ticker, name, segment, layer_of(segment), notes),
        )
        conn.commit()

    price_rows = 0
    if pull_prices:
        # Lazy import: yfinance pulls a network session at import time.
        from backend.etl import fetch_history, upsert_prices
        try:
            df = fetch_history(yahoo_ticker)
            price_rows = upsert_prices(ticker, df)
        except Exception as exc:  # noqa: BLE001 — surface, don't fail the add
            return {
                "ticker": ticker, "name": name, "segment": segment,
                "layer": layer_of(segment), "price_rows": 0, "priced": False,
                "price_error": str(exc),
            }

    return {
        "ticker": ticker, "name": name, "segment": segment,
        "layer": layer_of(segment), "price_rows": price_rows,
        "priced": price_rows > 0,
    }


def rebucket_company(ticker: str, segment: str) -> dict:
    """Move a company to a different value-chain segment (updates layer too)."""
    ticker = (ticker or "").strip().upper()
    segment = (segment or "").strip()
    if segment not in VALID_PORTFOLIO_SEGMENTS:
        raise WatchlistError(
            f"unknown segment {segment!r}; must be one of {VALID_PORTFOLIO_SEGMENTS}"
        )
    if ticker in BENCHMARK_TICKERS:
        raise WatchlistError(f"{ticker} is a benchmark and cannot be re-bucketed")

    init_schema()
    with connect() as conn:
        if not _exists(conn, ticker):
            raise WatchlistError(f"{ticker} is not on the watchlist")
        conn.execute(
            "UPDATE companies SET segment=?, layer=? WHERE ticker=?",
            (segment, layer_of(segment), ticker),
        )
        conn.commit()
    return {"ticker": ticker, "segment": segment, "layer": layer_of(segment)}


def remove_company(ticker: str) -> dict:
    """Remove a portfolio company and its derived data (prices, metrics, facts)."""
    ticker = (ticker or "").strip().upper()
    if ticker in BENCHMARK_TICKERS:
        raise WatchlistError(f"{ticker} is a benchmark and cannot be removed")

    init_schema()
    with connect() as conn:
        if not _exists(conn, ticker):
            raise WatchlistError(f"{ticker} is not on the watchlist")
        deleted = {}
        for table in ("prices", "fundamentals", "company_metrics", "companies"):
            cur = conn.execute(f"DELETE FROM {table} WHERE ticker=?", (ticker,))
            deleted[table] = cur.rowcount
        conn.commit()
    return {"ticker": ticker, "removed": True, "deleted": deleted}


def _usage() -> None:
    print(__doc__.strip().split("CLI:")[1])


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        _usage()
        sys.exit(1)
    cmd = args[0]
    try:
        if cmd == "list":
            for c in list_companies():
                flag = " [bench]" if c["is_benchmark"] else ""
                print(f"  L{c['layer']:<2} {c['ticker']:<8} {c['price_rows']:>5} px  "
                      f"{c['segment']:<32}{flag}  {c['name']}")
        elif cmd == "add" and len(args) >= 4:
            tkr, yh, nm, seg = args[1], args[2], args[3], args[4] if len(args) > 4 else ""
            # CLI form: add TICKER YAHOO NAME SEGMENT  (segment may contain spaces → join rest)
            seg = " ".join(args[4:]) if len(args) > 4 else ""
            print(add_company(tkr, nm, seg, yahoo_ticker=yh))
        elif cmd == "rebucket" and len(args) >= 3:
            print(rebucket_company(args[1], " ".join(args[2:])))
        elif cmd == "remove" and len(args) >= 2:
            print(remove_company(args[1]))
        else:
            _usage()
            sys.exit(1)
    except WatchlistError as e:
        print(f"error: {e}")
        sys.exit(2)
