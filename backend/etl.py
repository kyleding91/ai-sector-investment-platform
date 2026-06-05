"""Fetch ~11Y of daily adjusted closes from Yahoo Finance into SQLite.

Manual run:
    python -m backend.etl refresh            # pull all tickers
    python -m backend.etl refresh NVDA AMD   # pull a subset
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from backend.db import connect, init_schema, set_meta

YEARS_OF_HISTORY = 11   # buffer over 10Y to give slack on weekends/holidays
MIN_ROWS_OK = 200       # below this we suspect a stub series (or a recent IPO)
THROTTLE_SECONDS = 1.0  # polite delay between Yahoo requests
MAX_ATTEMPTS = 4        # retry on transient rate limits


def _make_session():
    """Browser-impersonating session helps avoid Yahoo's rate-limit gate."""
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate="chrome")
    except Exception:  # pragma: no cover - fallback if curl_cffi missing
        return None


_SESSION = _make_session()


def _download(ticker: str, period: str) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            t = yf.Ticker(ticker, session=_SESSION) if _SESSION else yf.Ticker(ticker)
            df = t.history(period=period, auto_adjust=True)
            if df is None or df.empty:
                return pd.DataFrame()
            return df[["Close"]].rename(columns={"Close": "adj_close"})
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc).lower()
            if "rate" in msg or "too many" in msg or "429" in msg:
                wait = 2 ** attempt
                print(f"    rate limited on {ticker}, sleeping {wait}s (attempt {attempt}/{MAX_ATTEMPTS})")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"exhausted retries for {ticker}: {last_exc}")


def fetch_history(yahoo_ticker: str) -> pd.DataFrame:
    return _download(yahoo_ticker, f"{YEARS_OF_HISTORY}y")


def upsert_prices(display_ticker: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = [
        (display_ticker, idx.strftime("%Y-%m-%d"), float(price))
        for idx, price in df["adj_close"].items()
        if pd.notna(price)
    ]
    with connect() as conn:
        conn.execute("DELETE FROM prices WHERE ticker = ?", (display_ticker,))
        conn.executemany(
            "INSERT INTO prices(ticker, date, adj_close) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    return len(rows)


def refresh(filter_tickers: list[str] | None = None) -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, yahoo_ticker, name FROM companies ORDER BY layer, ticker"
        ).fetchall()

    if filter_tickers:
        wanted = {t.upper() for t in filter_tickers}
        rows = [r for r in rows if r[0].upper() in wanted]

    print(f"Refreshing {len(rows)} ticker(s)...")
    failures: list[tuple[str, str]] = []
    for i, (display, yahoo, _name) in enumerate(rows, 1):
        try:
            df = fetch_history(yahoo)
            n = upsert_prices(display, df)
            first = df.index.min().date() if not df.empty else None
            last = df.index.max().date() if not df.empty else None
            print(f"  [{i:>2}/{len(rows)}] {display:<8} ({yahoo:<10}) {n:>5} rows  {first} -> {last}")
            if n == 0:
                failures.append((display, "no rows returned"))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:>2}/{len(rows)}] {display:<8} FAILED: {exc}")
            failures.append((display, str(exc)))
        time.sleep(THROTTLE_SECONDS)  # be polite to Yahoo

    set_meta("last_refresh_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"\nDone. Failures: {len(failures)}")
    for t, reason in failures:
        print(f"  {t}: {reason}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] != "refresh":
        print("Usage: python -m backend.etl refresh [TICKER ...]")
        sys.exit(1)
    refresh(args[1:] or None)
