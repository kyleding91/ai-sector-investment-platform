"""Unit tests for the offline extraction + metric helpers (no DB, no network)."""
from __future__ import annotations

from backend.filing_insights import (
    extract_mgmt_quotes,
    extract_self_description,
    html_to_text,
    short_name,
)
from backend.metrics import _cagr, _fy_label


def test_html_to_text_strips_tags_and_blocks():
    out = html_to_text(b"<p>Hello</p><div>World</div>")
    assert "Hello" in out and "World" in out
    assert "<" not in out and ">" not in out
    # block-level tags become paragraph breaks
    assert out == "Hello\n\nWorld"


def test_html_to_text_tolerates_garbage():
    # Malformed markup must not raise.
    assert isinstance(html_to_text(b"<<>not real html<<"), str)


def test_short_name():
    assert short_name("NVIDIA Corporation") == "NVIDIA"
    assert short_name("Micron Technology, Inc.") == "Micron"
    assert short_name("Arm Holdings plc") == "Arm"


def test_extract_mgmt_quotes_speaker_before():
    press = (
        'Jensen Huang, founder and CEO of NVIDIA, said, "Demand for our AI '
        'infrastructure is extraordinary and accelerating as enterprises modernize '
        'their data centers worldwide."'
    )
    quotes = extract_mgmt_quotes(press)
    assert len(quotes) == 1
    assert quotes[0]["speaker"].startswith("Jensen Huang")
    assert quotes[0]["quote"].startswith("Demand for our AI infrastructure")


def test_extract_mgmt_quotes_none_when_no_quote():
    assert extract_mgmt_quotes("A plain paragraph with no quotation at all.") == []


def test_extract_self_description():
    section = (
        "NVIDIA is a computing infrastructure company that designs and builds "
        "accelerated computing platforms for AI and data centers around the world. "
        "Our products are used by hyperscalers and enterprises."
    )
    desc = extract_self_description(section, "NVIDIA")
    assert desc is not None
    assert desc.lower().startswith("nvidia is a computing infrastructure")


def test_cagr_math():
    assert _cagr(100, 200, 1) == 1.0
    assert abs(_cagr(100, 200, 2) - 0.41421356237309515) < 1e-12


def test_cagr_undefined_cases():
    assert _cagr(0, 200, 1) is None        # zero start
    assert _cagr(100, -50, 1) is None       # negative end
    assert _cagr(100, 200, 0) is None       # non-positive years
    assert _cagr(None, 200, 1) is None      # missing value


def test_fy_label():
    assert _fy_label("2024-01-28") == "FY2024"
    assert _fy_label("2026-12-31") == "FY2026"
