"""SQLite connection + schema bootstrap."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ai_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker        TEXT PRIMARY KEY,        -- display ticker (e.g. NVDA, 005930)
    yahoo_ticker  TEXT NOT NULL,           -- ticker used on Yahoo (e.g. 005930.KS, ^SOX)
    name          TEXT NOT NULL,
    segment       TEXT NOT NULL,           -- value-chain segment (see companies.SEGMENTS)
    layer         INTEGER NOT NULL,        -- 1..9 value-chain layer; 99 for benchmarks
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker     TEXT NOT NULL,              -- references companies.ticker
    date       TEXT NOT NULL,              -- ISO date YYYY-MM-DD
    adj_close  REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Stage 1 of the fundamentals pipeline: raw financial facts (multi-source).
-- One row per (ticker, period, concept, source). EDGAR XBRL can join later
-- using the same shape, distinguished by `source`.
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker       TEXT NOT NULL,
    period_end   TEXT NOT NULL,            -- ISO date YYYY-MM-DD (fiscal period end)
    period_type  TEXT NOT NULL,            -- 'FY' (annual) for now
    concept      TEXT NOT NULL,            -- canonical concept (Revenue, NetIncome, ...)
    value        REAL,
    unit         TEXT,                     -- USD, USD/share, ...
    source       TEXT NOT NULL,            -- yfinance | edgar_xbrl | manual
    source_ref   TEXT,                     -- provenance string
    fetched_at   TEXT,
    PRIMARY KEY (ticker, period_end, period_type, concept, source)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker ON fundamentals(ticker);

-- Stage 2: panel-ready derived metrics. One row per ticker.
CREATE TABLE IF NOT EXISTS company_metrics (
    ticker                  TEXT PRIMARY KEY,
    revenue_latest          REAL,
    revenue_latest_period   TEXT,
    financial_currency      TEXT,          -- reporting currency of revenue_latest (TWD/KRW/EUR/USD)
    operating_margin        REAL,
    operating_margin_basis  TEXT,
    gross_margin            REAL,
    revenue_3y_cagr         REAL,
    revenue_cagr_window     TEXT,
    eps_3y_cagr             REAL,
    eps_cagr_window         TEXT,
    eps_cagr_caveat         TEXT,
    sources                 TEXT,
    last_updated_at         TEXT
);

-- Spot FX rates for normalizing foreign-filer revenue to USD. One row per
-- currency; `usd_per_unit` is how many USD 1 unit of the currency buys (so
-- USD = native * usd_per_unit). Refreshed from yfinance via backend.fx.
CREATE TABLE IF NOT EXISTS fx_rates (
    currency     TEXT PRIMARY KEY,         -- ISO code: TWD, KRW, EUR, ...
    usd_per_unit REAL NOT NULL,            -- 1 unit of currency = this many USD
    as_of        TEXT,                     -- rate observation date (YYYY-MM-DD)
    fetched_at   TEXT
);

-- Stage 3: LLM-generated structured insights. Full history kept; the API
-- serves the latest generation per (scope_type, scope_id).
CREATE TABLE IF NOT EXISTS insights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type    TEXT NOT NULL,           -- 'stock' (sector reserved for later)
    scope_id      TEXT NOT NULL,           -- ticker
    generated_at  TEXT NOT NULL,
    model         TEXT NOT NULL,
    schema_ver    INTEGER NOT NULL,
    content_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_insights_scope ON insights(scope_type, scope_id);

-- Phase 4: SEC EDGAR filings corpus. One row per filing (keyed by accession,
-- which is globally unique) so frequent 8-K/6-K filings never collide. Foreign
-- private issuers (TSM, ASML, UMC, ARM, NBIS) file 20-F/6-K instead of
-- 10-K/10-Q/8-K; Samsung/SK hynix don't file with the SEC at all and are skipped.
CREATE TABLE IF NOT EXISTS filings (
    ticker          TEXT NOT NULL,           -- references companies.ticker
    accession       TEXT NOT NULL,           -- e.g. '0000019617-26-000123'
    filing_type     TEXT NOT NULL,           -- 10-K | 10-Q | 8-K-earnings | 20-F | 6-K
    form            TEXT NOT NULL,           -- raw SEC form code
    period_end      TEXT,                    -- 'YYYY-MM-DD' fiscal period end (may be blank)
    filed_at        TEXT NOT NULL,           -- 'YYYY-MM-DD'
    primary_doc_url TEXT,
    local_path      TEXT,                    -- relative to project root
    size_bytes      INTEGER,
    sha256          TEXT,
    status          TEXT NOT NULL,           -- ok | failed
    validation_msg  TEXT,
    PRIMARY KEY (ticker, accession)
);
CREATE INDEX IF NOT EXISTS idx_filings_ticker ON filings(ticker, filing_type);
CREATE INDEX IF NOT EXISTS idx_filings_status ON filings(status);

-- Phase 4b: extractive "in their own words" narrative pulled straight from the
-- downloaded filings (no LLM). One row per ticker: the company's self-description
-- and business overview from the latest 10-K/20-F, plus management quotes from the
-- latest earnings release. content_json holds the structured payload.
CREATE TABLE IF NOT EXISTS filing_insights (
    ticker        TEXT PRIMARY KEY,          -- references companies.ticker
    source_form   TEXT,                      -- 10-K | 20-F (annual report parsed)
    source_period TEXT,                      -- fiscal period_end of that report
    source_url    TEXT,                      -- SEC primary-doc URL
    generated_at  TEXT,
    method        TEXT,                      -- 'extractive (SEC filings)'
    content_json  TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the original CREATE TABLE shipped. `CREATE TABLE IF NOT
# EXISTS` won't alter an existing table, so add any missing ones idempotently.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "company_metrics": [
        ("revenue_latest_usd", "REAL"),   # native revenue converted to USD
        ("fx_rate", "REAL"),              # usd_per_unit used for the conversion
        ("fx_rate_asof", "TEXT"),         # observation date of that rate
    ],
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


if __name__ == "__main__":
    init_schema()
    print(f"Initialized schema at {DB_PATH}")
