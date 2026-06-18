"""Unit tests for the annualized-return math (pure, no DB)."""
from __future__ import annotations

import pandas as pd

from backend.returns import _ann_return


def _series(pairs):
    dates = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([v for _, v in pairs], index=dates).sort_index()


def test_ann_return_one_year_doubling_then_half():
    s = _series([("2023-06-01", 100.0), ("2024-06-01", 150.0)])
    r = _ann_return(s, s.index.max(), 1)
    assert r is not None
    assert abs(r - 0.5) < 1e-9


def test_ann_return_three_year_geometric():
    # 100 -> 200 over exactly 3 years => CAGR = 2^(1/3) - 1
    s = _series([("2021-06-01", 100.0), ("2024-06-01", 200.0)])
    r = _ann_return(s, s.index.max(), 3)
    assert r is not None
    assert abs(r - (2 ** (1 / 3) - 1)) < 1e-9


def test_ann_return_insufficient_history_returns_none():
    s = _series([("2024-06-01", 100.0)])
    assert _ann_return(s, s.index.max(), 3) is None


def test_ann_return_empty_series_returns_none():
    assert _ann_return(pd.Series(dtype=float), pd.Timestamp("2024-06-01"), 1) is None
