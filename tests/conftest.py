"""Shared pytest fixtures + DB-state probes.

The suite is designed to be green both here (a populated SQLite DB) and on a
fresh checkout (empty/no DB): structural assertions always run, while
data-dependent assertions are guarded by the HAS_* flags below.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.db import connect, init_schema
from backend.main import app


def _table_count(table: str) -> int:
    try:
        with connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:  # noqa: BLE001 — missing table/db => treat as empty
        return 0


# Probed once at import; drive skipif markers in the API tests.
init_schema()
HAS_COMPANIES = _table_count("companies") > 0
HAS_PRICES = _table_count("prices") > 0
HAS_METRICS = _table_count("company_metrics") > 0
HAS_FILINGS = _table_count("filings") > 0


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)
