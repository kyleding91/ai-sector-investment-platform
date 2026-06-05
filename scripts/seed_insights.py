"""Hand-seed illustrative AI deep-dive panels so the insight UI works without an
ANTHROPIC_API_KEY.

These panels are ILLUSTRATIVE seed content authored offline — directionally
representative of each company's place in the AI value chain, NOT live-grounded
research. Source URLs point at each company's investor-relations landing page.
Once you set ANTHROPIC_API_KEY, regenerate any name with web-grounded analysis:

    python -m backend.insights refresh stock NVDA

Run:
    python -m scripts.seed_insights            # seed all bundled panels
    python -m scripts.seed_insights NVDA TSM   # seed a subset
"""
from __future__ import annotations

import sys

from backend.insights import StockPanel, save_insight

SEED_MODEL = "hand-seeded (illustrative)"
AS_OF = "2026-05-31"


def _ev(claim: str, url: str) -> dict:
    return {"claim": claim, "source_url": url}


PANELS: list[dict] = [
    {
        "ticker": "NVDA",
        "as_of": AS_OF,
        "tldr": ("NVIDIA remains the gravitational center of the AI buildout: data-center revenue "
                 "now dwarfs gaming, gross margins sit in the low-70s%, and Blackwell-class GPUs are "
                 "sold out well ahead of supply. The debate has shifted from demand to the durability "
                 "of that demand and to custom-silicon and supply competition."),
        "key_metric_name": "Data Center Revenue",
        "key_metric_3y_change": "~$15B → >$170B (annualized)",
        "key_metric_explanation": "Data-center compute is the single line that drives NVIDIA's revenue, margin, and valuation.",
        "drivers_1y": {
            "internal": [
                {"factor": "Blackwell ramp", "direction": "Tailwind",
                 "description": "The Blackwell generation ramped into volume, lifting ASPs and rack-scale (GB-series) system revenue.",
                 "evidence": [_ev("Blackwell is NVIDIA's flagship data-center platform", "https://nvidianews.nvidia.com/")]},
                {"factor": "Software & networking attach", "direction": "Tailwind",
                 "description": "NVLink/Spectrum-X networking and CUDA software deepen lock-in and raise revenue per GPU shipped.", "evidence": None},
            ],
            "macro": [
                {"factor": "Hyperscaler capex", "direction": "Tailwind",
                 "description": "Microsoft, Google, Amazon and Meta continued to raise AI capex, the bulk of which flows to accelerated compute.", "evidence": None},
                {"factor": "China export controls", "direction": "Headwind",
                 "description": "U.S. restrictions on advanced GPU sales to China cap a previously material slice of demand.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "CUDA ecosystem moat", "direction": "Tailwind",
                 "description": "A decade-plus of CUDA libraries and developer mindshare makes switching costly for AI workloads.", "evidence": None},
                {"factor": "Annual cadence", "direction": "Tailwind",
                 "description": "A shift to a yearly architecture cadence (Hopper → Blackwell → next) compresses competitors' catch-up windows.", "evidence": None},
            ],
            "macro": [
                {"factor": "Generative-AI inflection", "direction": "Tailwind",
                 "description": "The post-ChatGPT training and inference wave turned GPUs into the scarce input for frontier AI.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "Inference mix shift", "direction": "Tailwind",
                 "description": "As deployed models drive inference volume, recurring GPU demand may prove stickier than one-off training builds.", "evidence": None},
                {"factor": "Supply/lead times", "direction": "Mixed",
                 "description": "CoWoS advanced-packaging and HBM supply remain the binding constraints on how fast revenue can grow.", "evidence": None},
            ],
            "macro": [
                {"factor": "Custom ASIC competition", "direction": "Headwind",
                 "description": "Hyperscaler in-house accelerators (TPU, Trainium, MTIA, Maia) and Broadcom/Marvell ASICs target the most cost-sensitive workloads.", "evidence": None},
                {"factor": "AI capex digestion risk", "direction": "Headwind",
                 "description": "Any pause in hyperscaler spending would hit NVIDIA first given its position at the top of the stack.", "evidence": None},
            ],
        },
        "mgmt_guidance": "Management has guided to continued sequential data-center growth, constrained mainly by supply rather than demand.",
        "key_catalysts": ["Next-gen architecture launch", "Inference demand broadening beyond hyperscalers", "Sovereign-AI buildouts"],
        "key_risks": ["Hyperscaler capex digestion", "Custom-silicon share loss", "Export-control tightening", "HBM/packaging supply limits"],
    },
    {
        "ticker": "ASML",
        "as_of": AS_OF,
        "tldr": ("ASML is the sole supplier of EUV lithography, making it the indispensable bottleneck for "
                 "leading-edge logic and HBM-class DRAM. Bookings are lumpy quarter to quarter, but the High-NA "
                 "transition and AI-driven leading-edge capex underpin a multi-year backlog."),
        "key_metric_name": "Net Bookings / Backlog",
        "key_metric_3y_change": "Backlog sustained in the tens of €B",
        "key_metric_explanation": "Lithography orders are a leading indicator of fab investment one-to-two years out.",
        "drivers_1y": {
            "internal": [
                {"factor": "High-NA EUV shipments", "direction": "Tailwind",
                 "description": "First High-NA EUV systems shipped to leading-edge customers, opening the next node-scaling cycle.",
                 "evidence": [_ev("ASML is the only maker of EUV lithography systems", "https://www.asml.com/en/investors")]},
                {"factor": "Service & upgrade revenue", "direction": "Tailwind",
                 "description": "A large installed base drives recurring service and field-upgrade revenue that smooths system-sale cyclicality.", "evidence": None},
            ],
            "macro": [
                {"factor": "Leading-edge AI capex", "direction": "Tailwind",
                 "description": "TSMC/Samsung/Intel leading-edge investment for AI silicon supports EUV tool demand.", "evidence": None},
                {"factor": "China DUV normalization", "direction": "Headwind",
                 "description": "After a surge, China mature-node (DUV) demand normalizes and export rules limit the addressable mix.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "EUV monopoly", "direction": "Tailwind",
                 "description": "No competitor can supply EUV, giving ASML pricing power on the most critical fab tool.", "evidence": None},
            ],
            "macro": [
                {"factor": "Geographic fab buildout", "direction": "Tailwind",
                 "description": "Subsidized fab construction in the U.S., Europe and Japan broadened the customer base for litho tools.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "High-NA adoption pace", "direction": "Mixed",
                 "description": "Revenue timing hinges on how quickly customers move High-NA from pilot lines into volume.", "evidence": None},
            ],
            "macro": [
                {"factor": "HBM/DRAM EUV intensity", "direction": "Tailwind",
                 "description": "Advanced DRAM for HBM increasingly uses EUV layers, adding a memory leg to litho demand.", "evidence": None},
                {"factor": "Export-control regime", "direction": "Headwind",
                 "description": "Tightening rules on tool sales to China remain an overhang on the order book.", "evidence": None},
            ],
        },
        "mgmt_guidance": "ASML frames AI as the primary growth driver into the second half of the decade, with leading-edge demand outpacing mature nodes.",
        "key_catalysts": ["High-NA volume adoption", "HBM-driven DRAM litho demand", "Leading-edge capacity additions"],
        "key_risks": ["Order lumpiness", "China export restrictions", "Customer capex timing"],
    },
    {
        "ticker": "TSM",
        "as_of": AS_OF,
        "tldr": ("TSMC manufactures the overwhelming majority of the world's leading-edge AI silicon, including "
                 "NVIDIA, AMD and the hyperscalers' custom chips. Advanced nodes (≤5nm) and CoWoS packaging are "
                 "the chokepoints of the entire AI supply chain."),
        "key_metric_name": "Advanced-node (≤5nm) Revenue Mix",
        "key_metric_3y_change": "Roughly a third → over half of wafer revenue",
        "key_metric_explanation": "Leading-edge mix drives both TSMC's growth and its industry-leading gross margin.",
        "drivers_1y": {
            "internal": [
                {"factor": "3nm ramp", "direction": "Tailwind",
                 "description": "High-volume 3nm production lifted ASPs and advanced-node mix, supporting ~60% gross margin.",
                 "evidence": [_ev("TSMC reports leading-edge nodes as the majority of wafer revenue", "https://investor.tsmc.com/english")]},
                {"factor": "CoWoS capacity expansion", "direction": "Tailwind",
                 "description": "Aggressive advanced-packaging (CoWoS) expansion eased the binding constraint on AI accelerator supply.", "evidence": None},
            ],
            "macro": [
                {"factor": "AI accelerator demand", "direction": "Tailwind",
                 "description": "Demand from NVIDIA, AMD and custom-ASIC customers kept leading-edge capacity fully utilized.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "Process leadership", "direction": "Tailwind",
                 "description": "A durable node lead over Samsung and Intel made TSMC the default foundry for frontier silicon.", "evidence": None},
            ],
            "macro": [
                {"factor": "Geopolitical concentration", "direction": "Mixed",
                 "description": "Taiwan concentration is a strategic risk, prompting U.S./Japan/Germany fab diversification at higher cost.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "2nm (N2) ramp", "direction": "Tailwind",
                 "description": "The N2 node entering production extends the leading-edge franchise and pricing power.", "evidence": None},
                {"factor": "Overseas fab margins", "direction": "Headwind",
                 "description": "Arizona/Japan fabs dilute margins near-term versus Taiwan-based manufacturing.", "evidence": None},
            ],
            "macro": [
                {"factor": "Taiwan Strait risk", "direction": "Headwind",
                 "description": "Cross-strait tension is the tail risk markets attach to the world's most critical fab base.", "evidence": None},
            ],
        },
        "mgmt_guidance": "TSMC has guided to AI-related revenue growing at a high compound rate over the next several years, leading total company growth.",
        "key_catalysts": ["N2 ramp", "CoWoS capacity unlock", "Custom-ASIC customer wins"],
        "key_risks": ["Geopolitical/Taiwan risk", "Overseas-fab margin dilution", "Cyclical inventory corrections"],
    },
    {
        "ticker": "MU",
        "as_of": AS_OF,
        "tldr": ("Micron is the U.S. play on the HBM supercycle: AI accelerators need stacks of high-bandwidth "
                 "memory, and HBM is sold out with pricing set in advance. The result is a sharp margin recovery "
                 "off the prior memory downcycle's trough."),
        "key_metric_name": "HBM Revenue",
        "key_metric_3y_change": "Near-zero → multi-$B run-rate",
        "key_metric_explanation": "HBM is the highest-margin, AI-levered slice of Micron's DRAM mix and the key swing factor for earnings.",
        "drivers_1y": {
            "internal": [
                {"factor": "HBM3E ramp", "direction": "Tailwind",
                 "description": "Qualification and volume shipments of HBM3E into AI accelerators drove a steep mix and margin improvement.",
                 "evidence": [_ev("Micron has ramped HBM for AI data-center customers", "https://investors.micron.com/")]},
                {"factor": "Capacity allocation to HBM", "direction": "Mixed",
                 "description": "Shifting wafer capacity to HBM tightens conventional DRAM/NAND supply, supporting broad pricing.", "evidence": None},
            ],
            "macro": [
                {"factor": "Memory pricing recovery", "direction": "Tailwind",
                 "description": "DRAM/NAND prices rebounded sharply off the prior downcycle as AI absorbed supply.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "Technology node transitions", "direction": "Tailwind",
                 "description": "Advanced 1-beta/1-gamma DRAM nodes improved cost and enabled competitive HBM.", "evidence": None},
            ],
            "macro": [
                {"factor": "Memory cyclicality", "direction": "Mixed",
                 "description": "Micron's earnings swing violently with the commodity memory cycle — a 2023 trough to a 2025-26 peak.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "HBM4 roadmap", "direction": "Tailwind",
                 "description": "Next-gen HBM4 share and pre-sold capacity are the key levers for sustaining elevated margins.", "evidence": None},
            ],
            "macro": [
                {"factor": "Supply discipline vs. oversupply", "direction": "Headwind",
                 "description": "Industry capacity additions (Micron, Samsung, SK hynix) could tip pricing if AI demand cools.", "evidence": None},
            ],
        },
        "mgmt_guidance": "Management has described HBM as effectively sold out with pricing largely contracted ahead, supporting record data-center revenue.",
        "key_catalysts": ["HBM4 qualification", "Sustained AI memory demand", "Pre-sold HBM capacity"],
        "key_risks": ["Memory downcycle", "HBM share loss to SK hynix/Samsung", "Capex-driven oversupply"],
    },
    {
        "ticker": "AVGO",
        "as_of": AS_OF,
        "tldr": ("Broadcom has two AI engines: custom accelerators (XPUs) co-designed with hyperscalers, and "
                 "high-end networking silicon (Tomahawk/Jericho) that wires GPU clusters together. AI now drives "
                 "the semiconductor segment's growth, layered on top of sticky infrastructure software (VMware)."),
        "key_metric_name": "AI / Custom Silicon Revenue",
        "key_metric_3y_change": "Small → a large, fast-growing share of semi revenue",
        "key_metric_explanation": "Custom XPU and AI-networking revenue is the growth engine markets value Broadcom on.",
        "drivers_1y": {
            "internal": [
                {"factor": "Custom XPU ramps", "direction": "Tailwind",
                 "description": "Multiple hyperscaler custom-accelerator programs moved into volume, expanding the AI revenue base.",
                 "evidence": [_ev("Broadcom designs custom AI accelerators with hyperscaler customers", "https://investors.broadcom.com/")]},
                {"factor": "AI networking share", "direction": "Tailwind",
                 "description": "Tomahawk/Jericho Ethernet switching wins position Broadcom in the scale-out fabric for AI clusters.", "evidence": None},
            ],
            "macro": [
                {"factor": "Hyperscaler in-house silicon", "direction": "Tailwind",
                 "description": "The push toward custom accelerators to cut reliance on merchant GPUs directly benefits Broadcom's model.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "VMware integration", "direction": "Tailwind",
                 "description": "The VMware acquisition added a large, high-margin recurring software stream and reshaped the model.", "evidence": None},
                {"factor": "M&A and capital returns", "direction": "Tailwind",
                 "description": "A disciplined acquire-and-optimize playbook plus buybacks/dividends compounded free cash flow.", "evidence": None},
            ],
            "macro": [
                {"factor": "Ethernet vs. InfiniBand", "direction": "Tailwind",
                 "description": "The industry tilt toward open Ethernet for AI fabrics favors Broadcom's switching franchise.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "New XPU customers", "direction": "Tailwind",
                 "description": "Additional custom-silicon design wins would extend the AI revenue runway multiple years.", "evidence": None},
            ],
            "macro": [
                {"factor": "Customer concentration", "direction": "Headwind",
                 "description": "AI revenue leans on a handful of hyperscalers; program timing or insourcing shifts move the needle.", "evidence": None},
            ],
        },
        "mgmt_guidance": "Management has pointed to a large multi-year custom-accelerator opportunity across its lead hyperscaler customers.",
        "key_catalysts": ["New custom-XPU wins", "AI networking attach", "Software margin expansion"],
        "key_risks": ["Hyperscaler customer concentration", "Program timing", "Cyclical non-AI semis"],
    },
    {
        "ticker": "MSFT",
        "as_of": AS_OF,
        "tldr": ("Microsoft is the most direct large-cap proxy for monetizing AI: Azure growth is increasingly "
                 "AI-driven via the OpenAI partnership and Copilot, funded by one of the industry's largest capex "
                 "programs. The market is weighing AI revenue ramp against the margin drag of that buildout."),
        "key_metric_name": "Azure Revenue Growth (with AI contribution)",
        "key_metric_3y_change": "AI moving from ~0 to a growing share of Azure growth",
        "key_metric_explanation": "Azure is the line where Microsoft's AI investment converts into measurable revenue.",
        "drivers_1y": {
            "internal": [
                {"factor": "AI capacity buildout", "direction": "Mixed",
                 "description": "Record data-center capex expands Azure AI capacity but pressures near-term cloud gross margin and free cash flow.",
                 "evidence": [_ev("Microsoft reports Azure and AI capex in its quarterly results", "https://www.microsoft.com/en-us/investor")]},
                {"factor": "Copilot monetization", "direction": "Tailwind",
                 "description": "Copilot across Microsoft 365 and GitHub adds per-seat AI revenue on top of existing subscriptions.", "evidence": None},
            ],
            "macro": [
                {"factor": "Enterprise AI adoption", "direction": "Tailwind",
                 "description": "Enterprises moving AI workloads to the cloud supports Azure consumption growth.", "evidence": None},
            ],
        },
        "drivers_3y": {
            "internal": [
                {"factor": "OpenAI partnership", "direction": "Tailwind",
                 "description": "Early, deep OpenAI alignment gave Azure a differentiated frontier-model position.", "evidence": None},
            ],
            "macro": [
                {"factor": "Cloud secular shift", "direction": "Tailwind",
                 "description": "Ongoing migration of IT spend to public cloud underpinned durable Azure growth.", "evidence": None},
            ],
        },
        "drivers_forward": {
            "internal": [
                {"factor": "Custom silicon (Maia)", "direction": "Tailwind",
                 "description": "In-house Maia accelerators could improve AI unit economics and reduce GPU dependence over time.", "evidence": None},
                {"factor": "Capex digestion", "direction": "Headwind",
                 "description": "Sustaining elevated capex without a matching AI-revenue ramp would weigh on returns and margins.", "evidence": None},
            ],
            "macro": [
                {"factor": "AI demand durability", "direction": "Mixed",
                 "description": "The pace at which enterprise AI spend converts to recurring consumption is the key swing factor.", "evidence": None},
            ],
        },
        "mgmt_guidance": "Management has guided to continued strong Azure growth and rising capex to meet AI demand, with AI a growing contributor to cloud growth.",
        "key_catalysts": ["Copilot seat expansion", "Azure AI consumption ramp", "Maia silicon efficiency"],
        "key_risks": ["Capex outrunning AI revenue", "Cloud margin pressure", "OpenAI relationship/economics shifts"],
    },
]


def seed(tickers: list[str] | None = None) -> None:
    wanted = {t.upper() for t in tickers} if tickers else None
    n = 0
    for panel in PANELS:
        if wanted and panel["ticker"] not in wanted:
            continue
        validated = StockPanel(**panel).model_dump()  # enforce schema
        validated["_model"] = SEED_MODEL
        save_insight("stock", panel["ticker"], validated)
        print(f"  seeded {panel['ticker']}")
        n += 1
    print(f"Done. Seeded {n} illustrative panel(s). Regenerate with web grounding via "
          f"`python -m backend.insights refresh stock <TICKER>` once ANTHROPIC_API_KEY is set.")


if __name__ == "__main__":
    seed(sys.argv[1:] or None)
