"""Seed the companies table from backend.companies.COMPANIES."""
from __future__ import annotations

from backend.companies import COMPANIES, layer_of, validate
from backend.db import connect, init_schema


def seed() -> None:
    validate()
    init_schema()
    with connect() as conn:
        conn.execute("DELETE FROM companies")
        conn.executemany(
            "INSERT INTO companies(ticker, yahoo_ticker, name, segment, layer, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(t, y, n, s, layer_of(s), notes) for (t, y, n, s, notes) in COMPANIES],
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"Seeded {n} rows into companies table.")


if __name__ == "__main__":
    seed()
