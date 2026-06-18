"""Smoke tests for the FastAPI endpoints.

Structural shape is asserted unconditionally; assertions that depend on seeded
data are skipped when the corresponding table is empty (see conftest HAS_*).
All endpoints here are DB-backed and offline — /api/snapshot is only exercised
with a bogus ticker so no yfinance network call is made.
"""
from __future__ import annotations

import pytest

from tests.conftest import HAS_COMPANIES, HAS_FILINGS, HAS_METRICS, HAS_PRICES


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_companies_shape(client):
    r = client.get("/api/companies")
    assert r.status_code == 200
    body = r.json()
    assert "companies" in body and isinstance(body["companies"], list)
    for c in body["companies"]:
        assert {"ticker", "name", "segment", "layer"} <= c.keys()


@pytest.mark.skipif(not HAS_COMPANIES, reason="companies table empty")
def test_companies_has_nvda(client):
    tickers = {c["ticker"] for c in client.get("/api/companies").json()["companies"]}
    assert "NVDA" in tickers


def test_returns_shape(client):
    r = client.get("/api/returns")
    assert r.status_code == 200
    body = r.json()
    assert set(body["horizons"]) == {"1y", "3y", "5y", "10y"}
    assert isinstance(body["data"], list)


@pytest.mark.skipif(not HAS_PRICES, reason="prices table empty")
def test_returns_primary_benchmark_is_sox(client):
    body = client.get("/api/returns").json()
    assert body["primary_benchmark"] is not None
    assert body["primary_benchmark"]["ticker"] == "SOX"
    nvda = [r for r in body["data"] if r["ticker"] == "NVDA"]
    assert nvda, "NVDA missing from returns data"
    assert set(nvda[0]["returns"]) == {"1y", "3y", "5y", "10y"}


@pytest.mark.skipif(not HAS_METRICS, reason="company_metrics table empty")
def test_metrics_nvda(client):
    body = client.get("/api/metrics/NVDA").json()
    assert body["exists"] is True
    assert body.get("revenue_latest") is not None


def test_metrics_unknown_ticker(client):
    body = client.get("/api/metrics/ZZZZ").json()
    assert body == {"ticker": "ZZZZ", "exists": False}


@pytest.mark.skipif(not HAS_FILINGS, reason="filings table empty")
def test_filings_nvda(client):
    body = client.get("/api/filings/NVDA").json()
    assert body["ticker"] == "NVDA"
    assert isinstance(body["filings"], list) and body["filings"]
    for f in body["filings"]:
        assert {"filing_type", "filed_at"} <= f.keys()


def test_filing_insights_unknown_ticker(client):
    body = client.get("/api/filing-insights/ZZZZ").json()
    assert body["exists"] is False


def test_snapshot_unknown_ticker_is_offline(client):
    # Bogus ticker => exists:false before any yfinance call, so this stays offline.
    body = client.get("/api/snapshot/ZZZZ").json()
    assert body == {"ticker": "ZZZZ", "exists": False}
