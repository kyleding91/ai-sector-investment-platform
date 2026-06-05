"""Annualized total-return calculations from cached daily adjusted closes.

`adj_close` from yfinance auto_adjust=True already includes splits + dividends,
so geometric return on this series IS total return. (Index benchmarks like ^SOX
are price-only, and the foreign rows are in local currency — see README.)

CLI:
    python -m backend.returns           # full table
    python -m backend.returns NVDA      # one ticker
"""
from __future__ import annotations

import sys

import pandas as pd

from backend.db import connect

HORIZONS_YEARS = (1, 3, 5, 10)


def _load_prices() -> dict[str, pd.Series]:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT ticker, date, adj_close FROM prices ORDER BY ticker, date",
            conn,
            parse_dates=["date"],
        )
    out: dict[str, pd.Series] = {}
    for tkr, grp in df.groupby("ticker"):
        out[tkr] = grp.set_index("date")["adj_close"].sort_index()
    return out


def _ann_return(series: pd.Series, end_dt: pd.Timestamp, years: int) -> float | None:
    """Annualized return between (end_dt - years) and end_dt.

    Picks the trading day on-or-after the target start date so weekends/holidays
    don't blow up. Returns None if there isn't enough history.
    """
    if series.empty:
        return None
    start_target = end_dt - pd.DateOffset(years=years)
    eligible = series[series.index >= start_target]
    if eligible.empty:
        return None
    start_dt = eligible.index[0]
    if start_dt > start_target + pd.Timedelta(days=14):
        return None
    start_price = float(series.loc[start_dt])
    end_price = float(series.loc[end_dt])
    if start_price <= 0:
        return None
    return (end_price / start_price) ** (1.0 / years) - 1.0


def compute_table() -> pd.DataFrame:
    prices = _load_prices()
    with connect() as conn:
        meta = pd.read_sql_query(
            "SELECT ticker, name, segment, layer FROM companies ORDER BY layer, ticker", conn
        )

    rows = []
    for _, m in meta.iterrows():
        tkr = m["ticker"]
        s = prices.get(tkr)
        base = {"ticker": tkr, "name": m["name"], "segment": m["segment"], "layer": int(m["layer"])}
        if s is None or s.empty:
            rows.append({**base, "as_of": None, **{f"{y}y": None for y in HORIZONS_YEARS}})
            continue
        end_dt = s.index.max()
        row = {**base, "as_of": end_dt.strftime("%Y-%m-%d")}
        for y in HORIZONS_YEARS:
            row[f"{y}y"] = _ann_return(s, end_dt, y)
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt_pct(v) -> str:
    return "  N/A " if v is None or pd.isna(v) else f"{v * 100:+6.2f}%"


def print_table(filter_tickers: list[str] | None = None) -> None:
    df = compute_table()
    if filter_tickers:
        df = df[df["ticker"].isin([t.upper() for t in filter_tickers])]
    print(f"{'Ticker':<8}{'Segment':<32}{'1Y':>9}{'3Y':>9}{'5Y':>9}{'10Y':>9}  Name")
    print("-" * 110)
    for _, r in df.iterrows():
        print(
            f"{r['ticker']:<8}{r['segment']:<32}"
            f"{_fmt_pct(r['1y']):>9}{_fmt_pct(r['3y']):>9}"
            f"{_fmt_pct(r['5y']):>9}{_fmt_pct(r['10y']):>9}  "
            f"{r['name']}"
        )


if __name__ == "__main__":
    print_table(sys.argv[1:] or None)
