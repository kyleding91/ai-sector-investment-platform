"""Spot FX rates for normalizing foreign-filer revenue to USD.

Foreign private issuers report in their statement currency — TSM/UMC in TWD,
Samsung/SK hynix in KRW, ASML in EUR. To show a USD-comparable revenue figure
alongside the native one, we store a spot rate per currency (fetched from Yahoo
Finance, the same source the rest of the app uses) and let `metrics.py` apply it.

This is a SPOT rate, not a period-average — a revenue figure earned over a fiscal
year is converted at one recent rate, so the USD number is an approximation for
cross-filer comparison, not a reported GAAP/IFRS figure. The UI says as much.

CLI:
    python -m backend.fx refresh        # refresh all needed currencies
    python -m backend.fx refresh TWD EUR
    python -m backend.fx show
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from backend.db import connect, init_schema

# yfinance quotes 'XXXUSD=X' as USD per 1 unit of XXX — exactly our usd_per_unit.
def _pair(currency: str) -> str:
    return f"{currency.upper()}USD=X"


def needed_currencies() -> list[str]:
    """Distinct non-USD reporting currencies present in the fundamentals table."""
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT unit FROM fundamentals WHERE unit IS NOT NULL"
        ).fetchall()
    out: set[str] = set()
    for (unit,) in rows:
        cur = unit.split("/")[0].strip().upper()  # 'TWD/share' -> 'TWD'
        if cur and cur != "USD" and cur.isalpha() and len(cur) == 3:
            out.add(cur)
    return sorted(out)


def _fetch_one(currency: str) -> tuple[float, str] | None:
    """Return (usd_per_unit, as_of_date) for a currency, or None on failure."""
    try:
        import yfinance as yf
        from backend.etl import _SESSION
        t = yf.Ticker(_pair(currency), session=_SESSION) if _SESSION else yf.Ticker(_pair(currency))
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None
        last = hist.iloc[-1]
        rate = float(last["Close"])
        if rate <= 0:
            return None
        as_of = hist.index[-1].strftime("%Y-%m-%d")
        return rate, as_of
    except Exception:  # noqa: BLE001
        return None


def refresh(currencies: list[str] | None = None) -> None:
    init_schema()
    targets = [c.upper() for c in currencies] if currencies else needed_currencies()
    if not targets:
        print("No non-USD currencies to refresh.")
        return
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Refreshing FX rates for: {', '.join(targets)}")
    rows: list[tuple] = []
    for cur in targets:
        res = _fetch_one(cur)
        if res is None:
            print(f"  {cur}: FAILED (no rate)")
            continue
        rate, as_of = res
        rows.append((cur, rate, as_of, fetched_at))
        print(f"  {cur}: 1 {cur} = {rate:.6f} USD  (as of {as_of})")
    if rows:
        with connect() as conn:
            conn.executemany(
                "INSERT INTO fx_rates(currency, usd_per_unit, as_of, fetched_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(currency) DO UPDATE SET "
                "usd_per_unit=excluded.usd_per_unit, as_of=excluded.as_of, "
                "fetched_at=excluded.fetched_at",
                rows,
            )
            conn.commit()
    print(f"Done. {len(rows)} rate(s) stored.")


def get_rate(currency: str) -> tuple[float, str | None] | None:
    """(usd_per_unit, as_of) for a currency. USD is identity; None if unknown."""
    if not currency or currency.upper() == "USD":
        return (1.0, None)
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT usd_per_unit, as_of FROM fx_rates WHERE currency=?",
            (currency.upper(),),
        ).fetchone()
    if not row:
        return None
    return (float(row[0]), row[1])


def show() -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT currency, usd_per_unit, as_of, fetched_at FROM fx_rates "
            "ORDER BY currency"
        ).fetchall()
    if not rows:
        print("No FX rates stored. Run: python -m backend.fx refresh")
        return
    print(f"{'CCY':<5}{'USD per unit':>16}  {'AS OF':<12}FETCHED")
    for cur, rate, as_of, fetched in rows:
        print(f"{cur:<5}{rate:>16.6f}  {as_of or '—':<12}{fetched or ''}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            "  python -m backend.fx refresh [CCY ...]\n"
            "  python -m backend.fx show"
        )
        return
    cmd = args[0]
    if cmd == "refresh":
        refresh(args[1:] or None)
    elif cmd == "show":
        show()
    else:
        raise SystemExit(f"Unknown: {cmd!r}")


if __name__ == "__main__":
    main()
