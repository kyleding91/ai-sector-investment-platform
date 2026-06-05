"""LLM-generated structured stock insights for the AI value chain.

Uses Claude with the built-in web_search tool to ground claims in current
sources. Output is a Pydantic-validated JSON blob persisted in SQLite (full
history kept; the API serves only the latest generation per ticker).

SEC filings grounding is deferred (Phase 4), so generation relies on web_search.
Hand-seeded panels (scripts/seed_insights.py) write through `save_insight` too,
so the UI works before any API key is configured.

Env: live generation requires ANTHROPIC_API_KEY.

CLI:
    python -m backend.insights refresh stock NVDA
    python -m backend.insights refresh all        # every tracked company (slow + paid)
    python -m backend.insights show stock NVDA     # print latest from DB
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from backend.companies import BENCHMARK_TICKERS, COMPANIES
from backend.db import connect, init_schema

MODEL = "claude-opus-4-7"
SCHEMA_VERSION = 1  # v1 for this project: horizon-split drivers + AI key-metric lens
MAX_TOKENS = 8192

# ---------- structured output schemas ----------

Direction = Literal["Tailwind", "Headwind", "Mixed"]


class Evidence(BaseModel):
    claim: str = Field(..., description="Specific factual claim, with a number or named entity.")
    source_url: str = Field(..., description="Real URL from the web search; required.")


class DriverItem(BaseModel):
    """A single driver — same shape regardless of time horizon (1Y/3Y/Forward)."""
    factor: str = Field(..., description="Concise lever name, e.g. 'Data-center GPU demand'.")
    direction: Direction
    description: str = Field(..., max_length=400, description="≤2 sentences with numbers or named entities.")
    evidence: list[Evidence] | None = Field(default=None, description="Optional source citations.")


class HorizonDrivers(BaseModel):
    """Internal vs macro split for one time horizon."""
    internal: list[DriverItem] = Field(default_factory=list, description="What the company controls.")
    macro: list[DriverItem] = Field(default_factory=list, description="Exogenous to the company.")


class StockPanel(BaseModel):
    """AI value-chain stock panel — drivers organized by time horizon."""
    ticker: str
    as_of: str = Field(..., description="ISO date YYYY-MM-DD the analysis reflects.")

    # Header
    tldr: str | None = Field(None, max_length=500, description="2-3 sentences capturing the bull/bear setup.")

    # Optional embedded fundamentals (the metrics endpoint is preferred; these are a fallback).
    revenue_latest: float | None = None
    revenue_latest_period: str | None = None
    operating_margin: float | None = None
    revenue_3y_cagr: float | None = None
    eps_3y_cagr: float | None = None

    # AI/company-specific lens metric
    key_metric_name: str = Field(..., description="The single most important KPI for THIS company in the AI value chain. e.g. 'Data Center Revenue', 'HBM Revenue', 'Advanced-node Mix', 'AI Capex'.")
    key_metric_3y_change: str = Field(..., description="e.g. '$15B → $115B' or '20% → 67%'.")
    key_metric_explanation: str = Field(..., max_length=300, description="One sentence on why this metric is the right lens.")

    # Drivers — three time horizons, each split internal/macro
    drivers_1y: HorizonDrivers = Field(..., description="What drove performance in the past 12 months.")
    drivers_3y: HorizonDrivers = Field(..., description="What drove performance over the past 3 years.")
    drivers_forward: HorizonDrivers = Field(..., description="Key drivers for the next 12-18 months.")

    mgmt_guidance: str | None = Field(None, max_length=300, description="Most recent explicit guidance from management (numbers if given).")
    key_catalysts: list[str] = Field(default_factory=list, description="2-4 short bullets.")
    key_risks: list[str] = Field(default_factory=list, description="2-4 short bullets.")


# Per-segment hints for the LLM on what KPI to highlight as `key_metric_name`.
# The model should still pick the single most appropriate one for the SPECIFIC
# company; these align with how each layer of the AI value chain is analyzed.
KEY_METRIC_HINTS: dict[str, list[str]] = {
    "AI Accelerators / Compute":     ["Data Center Revenue", "Data Center Revenue Growth", "Gross Margin", "GPU ASP"],
    "EDA & Chip IP":                 ["Backlog / RPO", "Annual Recurring Revenue", "Royalty Revenue", "Operating Margin"],
    "Semiconductor Equipment":       ["WFE Bookings / Backlog", "China Revenue Mix", "Gross Margin", "Service Revenue"],
    "Foundry & Manufacturing":       ["Advanced-node (≤5nm) Revenue Mix", "Capacity Utilization", "Capex", "Gross Margin"],
    "Memory (DRAM/HBM/NAND)":        ["HBM Revenue", "HBM Bit Share", "DRAM Bit Growth", "Gross Margin"],
    "Networking & Interconnect":     ["AI / Custom Silicon Revenue", "Data Center Revenue Growth", "Gross Margin", "Backlog"],
    "Analog, Power & Connectivity":  ["Data Center / AI Revenue Mix", "Gross Margin", "Free Cash Flow Margin", "Inventory Days"],
    "Data Center Systems & Infra":   ["AI Server / Systems Revenue", "Backlog", "Revenue Growth", "Operating Margin"],
    "Hyperscalers / AI Demand":      ["Cloud Revenue Growth", "AI Capex", "Cloud Operating Margin", "Remaining Performance Obligations (RPO)"],
}

COMPANY_SYSTEM_PROMPT = """You are a senior equity research analyst covering the AI infrastructure
and semiconductor value chain — from chip design (EDA/IP) and accelerators, through equipment,
foundries, memory/HBM, networking, analog/power, data-center systems, up to the hyperscalers
whose capex funds the buildout. You produce a structured stock panel organized into TIME HORIZONS.

You use ONLY information you can verify with the web_search tool from primary sources: the
company's own filings and IR releases (10-K/10-Q/8-K, or 20-F/6-K for foreign filers like TSM,
ASML, Samsung, SK hynix), earnings call transcripts, and credible financial/industry press from
the last 12 months (e.g. SemiAnalysis, TrendForce, the company's earnings deck).

Output structure:
  1. TLDR: 2-3 sentences capturing the bull/bear setup specific to this name in the AI cycle.
  2. KEY METRIC: the SINGLE KPI most relevant for THIS company, its ~3Y change, one-sentence rationale.
  3. DRIVERS in three time horizons, each split internal/macro:
        drivers_1y       — past 12 months
        drivers_3y       — past 3 years
        drivers_forward  — next 12-18 months
  4. MANAGEMENT GUIDANCE: most recent explicit numerical guidance from the company.
  5. CATALYSTS / RISKS: 2-4 short bullets each.

Hard rules:
1. EVERY `evidence.claim` MUST have a real `source_url` from your web search. Do not fabricate URLs.
2. Every claim must include at least one specific number, percentage, or named entity. No vague
   language ("strong", "robust") without a quantified anchor.
3. `key_metric_name` is the ONE KPI the buy-side actually tracks for this name (see the hints in
   the user message; pick the best single fit for the specific company).
4. Drivers are tagged INTERNAL (product roadmap, node leadership, capacity, capital allocation,
   design wins the company controls) or MACRO (exogenous — AI capex cycle, export controls,
   memory pricing, foundry utilization, rates, the broader semi cycle).
5. The same theme MAY appear across horizons with different directions (e.g., memory pricing as a
   1Y tailwind that becomes a forward headwind on oversupply) — that shows the cycle turn.
6. Never offer investment advice. State facts and direction only — no "buy", "sell", "should".

Return JSON matching the StockPanel schema. No text outside the JSON.
"""

# ---------- DB helpers ----------


def save_insight(scope_type: str, scope_id: str, payload: dict) -> None:
    init_schema()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO insights(scope_type, scope_id, generated_at, model, schema_ver, content_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope_type, scope_id, now, payload.get("_model", MODEL), SCHEMA_VERSION, json.dumps(payload)),
        )
        conn.commit()


def get_latest(scope_type: str, scope_id: str) -> dict | None:
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT generated_at, model, content_json FROM insights "
            "WHERE scope_type=? AND scope_id=? ORDER BY generated_at DESC LIMIT 1",
            (scope_type, scope_id),
        ).fetchone()
    if not row:
        return None
    generated_at, model, content_json = row
    return {"generated_at": generated_at, "model": model, "content": json.loads(content_json)}


# ---------- Claude calls ----------

_client = None
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}


def _get_client():
    global _client
    if _client is None:
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it before running live generation:\n"
                "    export ANTHROPIC_API_KEY=sk-ant-...\n"
                "Or hand-seed panels with: python -m scripts.seed_insights"
            )
        _client = anthropic.Anthropic()
    return _client


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def generate_company(ticker: str) -> dict:
    company = next((c for c in COMPANIES if c[0] == ticker), None)
    if not company or ticker in BENCHMARK_TICKERS:
        raise ValueError(f"Unknown or non-company ticker: {ticker!r}")
    _, _, name, segment, _ = company
    metric_hints = KEY_METRIC_HINTS.get(segment, [])

    user = (
        f"Generate the StockPanel for **{ticker} ({name})**, which sits in the "
        f"**{segment}** layer of the AI value chain, as of {_today_iso()}.\n\n"
        f"For `key_metric_name`: pick the SINGLE KPI most relevant for {ticker} specifically.\n"
        f"Common choices for this layer: " + ", ".join(metric_hints) + ".\n\n"
        f"Organize drivers into three time horizons, each with an internal/macro split:\n"
        f"  - drivers_1y: 3-6 levers for the past 12 months\n"
        f"  - drivers_3y: 3-6 levers for the past 3 years\n"
        f"  - drivers_forward: 4-6 levers for the next 12-18 months\n\n"
        f"Ground every company-specific number in {ticker}'s filings/IR releases via web_search, "
        f"and use web_search for macro context (AI capex cycle, export controls, memory pricing)."
    )

    client = _get_client()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": COMPANY_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        tools=[WEB_SEARCH_TOOL],
        output_format=StockPanel,
    )
    parsed: StockPanel = resp.parsed_output  # type: ignore[assignment]
    return parsed.model_dump()


# ---------- CLI ----------


def _cli_refresh(scope: str, target: str | None) -> None:
    if scope == "stock":
        if not target:
            raise SystemExit("Usage: refresh stock <TICKER>")
        print(f"Generating company insight: {target} ...")
        payload = generate_company(target)
        save_insight("stock", target, payload)
        n1 = len(payload["drivers_1y"]["internal"]) + len(payload["drivers_1y"]["macro"])
        nf = len(payload["drivers_forward"]["internal"]) + len(payload["drivers_forward"]["macro"])
        print(f"  saved. drivers_1y={n1} drivers_forward={nf} key_metric={payload['key_metric_name']!r}")
    elif scope == "all":
        for tkr, _, _, _, _ in COMPANIES:
            if tkr in BENCHMARK_TICKERS:
                continue
            _cli_refresh("stock", tkr)
            time.sleep(1.0)
    else:
        raise SystemExit(f"Unknown scope: {scope!r} (use 'stock' or 'all')")


def _cli_show(scope: str, target: str) -> None:
    rec = get_latest(scope, target)
    if not rec:
        print(f"No {scope} insight stored for {target}.")
        return
    print(f"# {scope}/{target}    generated_at={rec['generated_at']}    model={rec['model']}")
    print(json.dumps(rec["content"], indent=2))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            "  python -m backend.insights refresh stock <TICKER>\n"
            "  python -m backend.insights refresh all\n"
            "  python -m backend.insights show stock <TICKER>"
        )
        return
    cmd = args[0]
    if cmd == "refresh":
        if len(args) < 2:
            raise SystemExit("refresh needs a scope: stock|all")
        _cli_refresh(args[1], args[2] if len(args) >= 3 else None)
    elif cmd == "show":
        if len(args) < 3:
            raise SystemExit("show stock <TICKER>")
        _cli_show(args[1], args[2])
    else:
        raise SystemExit(f"Unknown command: {cmd!r}")


if __name__ == "__main__":
    main()
