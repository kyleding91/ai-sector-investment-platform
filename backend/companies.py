"""Single source of truth: which tickers we track and where they sit in the AI value chain.

The watchlist is organized as a layered value chain — from chip design at the
top to the hyperscalers that buy the compute at the bottom. Each company gets
ONE primary segment; cross-cutting names (AVGO, INTC, VRT) carry a note.

Edit this file to add/re-bucket a company, then rerun `python -m backend.seed`.

Tuple shape: (display_ticker, yahoo_ticker, name, segment, notes)
`layer` is derived from the segment via SEGMENT_LAYER below.
"""
from __future__ import annotations

# Ordered segments = the value-chain layers, top (design) to bottom (demand).
SEGMENTS: list[str] = [
    "AI Accelerators / Compute",
    "EDA & Chip IP",
    "Semiconductor Equipment",
    "Foundry & Manufacturing",
    "Memory (DRAM/HBM/NAND)",
    "Networking & Interconnect",
    "Analog, Power & Connectivity",
    "Data Center Systems & Infra",
    "Hyperscalers / AI Demand",
]

# segment -> 1-based layer number (used to order the heat map)
SEGMENT_LAYER: dict[str, int] = {seg: i + 1 for i, seg in enumerate(SEGMENTS)}

# (display_ticker, yahoo_ticker, name, segment, notes)
COMPANIES: list[tuple[str, str, str, str, str]] = [
    # === Layer 1 — AI Accelerators / Compute (the GPUs/ASICs that run AI) ===
    ("NVDA", "NVDA", "NVIDIA Corporation",                       "AI Accelerators / Compute", "Dominant AI GPU + CUDA ecosystem."),
    ("AMD",  "AMD",  "Advanced Micro Devices, Inc.",             "AI Accelerators / Compute", "MI-series GPUs + EPYC CPUs."),
    ("INTC", "INTC", "Intel Corporation",                        "AI Accelerators / Compute", "Also a foundry (Layer 4); Gaudi accelerators."),

    # === Layer 2 — EDA & Chip IP (design tools + licensable cores) ===
    ("SNPS", "SNPS", "Synopsys, Inc.",                           "EDA & Chip IP", ""),
    ("CDNS", "CDNS", "Cadence Design Systems, Inc.",             "EDA & Chip IP", ""),
    ("ARM",  "ARM",  "Arm Holdings plc",                         "EDA & Chip IP", "ADR; CPU/GPU IP licensing. IPO Sep 2023 — short history."),

    # === Layer 3 — Semiconductor Equipment / WFE (tools that build the fabs) ===
    ("ASML", "ASML", "ASML Holding N.V.",                        "Semiconductor Equipment", "ADR; EUV lithography monopoly."),
    ("AMAT", "AMAT", "Applied Materials, Inc.",                  "Semiconductor Equipment", ""),
    ("LRCX", "LRCX", "Lam Research Corporation",                 "Semiconductor Equipment", ""),
    ("KLAC", "KLAC", "KLA Corporation",                          "Semiconductor Equipment", "Process control / inspection."),
    ("TER",  "TER",  "Teradyne, Inc.",                           "Semiconductor Equipment", "Automated test."),

    # === Layer 4 — Foundry & Manufacturing (where chips are made) ===
    ("TSM",  "TSM",  "Taiwan Semiconductor Manufacturing Co.",   "Foundry & Manufacturing", "ADR; leading-edge foundry."),
    ("GFS",  "GFS",  "GlobalFoundries Inc.",                     "Foundry & Manufacturing", "IPO Oct 2021 — short history."),
    ("UMC",  "UMC",  "United Microelectronics Corporation",      "Foundry & Manufacturing", "ADR; mature-node foundry."),

    # === Layer 5 — Memory (HBM is the AI bottleneck) ===
    ("MU",   "MU",   "Micron Technology, Inc.",                  "Memory (DRAM/HBM/NAND)", ""),
    ("005930", "005930.KS", "Samsung Electronics Co., Ltd.",     "Memory (DRAM/HBM/NAND)", "Korea listing — prices in KRW (local-currency total return). No SEC filings."),
    ("000660", "000660.KS", "SK hynix Inc.",                     "Memory (DRAM/HBM/NAND)", "Korea listing — prices in KRW (local-currency total return). HBM leader. No SEC filings."),

    # === Layer 6 — Networking & Interconnect (wiring GPUs into clusters) ===
    ("AVGO", "AVGO", "Broadcom Inc.",                            "Networking & Interconnect", "Also custom AI accelerators (overlaps Layer 1)."),
    ("MRVL", "MRVL", "Marvell Technology, Inc.",                 "Networking & Interconnect", "Custom silicon + optical DSPs."),
    ("ANET", "ANET", "Arista Networks, Inc.",                    "Networking & Interconnect", "Data-center switching."),
    ("CSCO", "CSCO", "Cisco Systems, Inc.",                      "Networking & Interconnect", ""),
    ("ALAB", "ALAB", "Astera Labs, Inc.",                        "Networking & Interconnect", "Connectivity ICs. IPO Mar 2024 — short history."),
    ("CRDO", "CRDO", "Credo Technology Group Holding Ltd",       "Networking & Interconnect", "Active electrical cables. IPO Jan 2022 — short history."),

    # === Layer 7 — Analog, Power & Connectivity (powering/cooling the silicon) ===
    ("MPWR", "MPWR", "Monolithic Power Systems, Inc.",           "Analog, Power & Connectivity", "Power delivery for GPUs."),
    ("ADI",  "ADI",  "Analog Devices, Inc.",                     "Analog, Power & Connectivity", ""),
    ("TXN",  "TXN",  "Texas Instruments Incorporated",           "Analog, Power & Connectivity", ""),
    ("NXPI", "NXPI", "NXP Semiconductors N.V.",                  "Analog, Power & Connectivity", ""),
    ("ON",   "ON",   "ON Semiconductor Corporation",             "Analog, Power & Connectivity", "onsemi; power."),

    # === Layer 8 — Data Center Systems & Infra (servers, power, cooling, neoclouds) ===
    ("SMCI", "SMCI", "Super Micro Computer, Inc.",               "Data Center Systems & Infra", "AI server OEM."),
    ("DELL", "DELL", "Dell Technologies Inc.",                   "Data Center Systems & Infra", "AI server OEM."),
    ("VRT",  "VRT",  "Vertiv Holdings Co",                       "Data Center Systems & Infra", "Power & thermal management / cooling."),
    ("CRWV", "CRWV", "CoreWeave, Inc.",                          "Data Center Systems & Infra", "GPU neocloud. IPO Mar 2025 — very short history."),
    ("NBIS", "NBIS", "Nebius Group N.V.",                        "Data Center Systems & Infra", "GPU neocloud. Listed 2024 — short history."),

    # === Layer 9 — Hyperscalers / AI Demand (the buyers driving capex) ===
    ("MSFT", "MSFT", "Microsoft Corporation",                    "Hyperscalers / AI Demand", "Azure; OpenAI partner; Maia accelerator."),
    ("GOOGL","GOOGL","Alphabet Inc.",                            "Hyperscalers / AI Demand", "Google Cloud; TPU accelerators."),
    ("AMZN", "AMZN", "Amazon.com, Inc.",                         "Hyperscalers / AI Demand", "AWS; Trainium/Inferentia accelerators."),
    ("META", "META", "Meta Platforms, Inc.",                     "Hyperscalers / AI Demand", "Largest merchant GPU buyer; MTIA accelerator."),
    ("ORCL", "ORCL", "Oracle Corporation",                       "Hyperscalers / AI Demand", "OCI; large AI capacity buildout."),

    # === Benchmarks (rendered as pinned top rows, not bucketed cells) ===
    ("SOX",     "^SOX",     "PHLX Semiconductor Index",          "Benchmark", "Primary benchmark — heat map is colored vs SOX."),
    ("SP500TR", "^SP500TR", "S&P 500 Total Return Index",        "Benchmark", "Broad-market reference (dividends reinvested)."),
]

VALID_SEGMENTS = set(SEGMENTS) | {"Benchmark"}

# Tickers that are benchmarks, not portfolio companies — excluded from the main grid.
BENCHMARK_TICKERS = {"SOX", "SP500TR"}
# The one used to color the heat map (deviation from this index).
PRIMARY_BENCHMARK = "SOX"


def layer_of(segment: str) -> int:
    """1-based value-chain layer for a segment; 99 for the Benchmark pseudo-segment."""
    return SEGMENT_LAYER.get(segment, 99)


def validate() -> None:
    seen: set[str] = set()
    for tkr, _, _, seg, _ in COMPANIES:
        assert seg in VALID_SEGMENTS, f"{tkr}: invalid segment {seg!r}"
        assert tkr not in seen, f"duplicate ticker {tkr}"
        seen.add(tkr)
    for b in BENCHMARK_TICKERS:
        assert b in seen, f"benchmark {b} missing from COMPANIES"
    assert PRIMARY_BENCHMARK in BENCHMARK_TICKERS


if __name__ == "__main__":
    validate()
    from collections import Counter
    counts = Counter(c[3] for c in COMPANIES)
    total = sum(v for s, v in counts.items() if s != "Benchmark")
    print(f"{total} companies + {counts.get('Benchmark', 0)} benchmarks\n")
    for seg in SEGMENTS:
        print(f"  L{SEGMENT_LAYER[seg]} {seg:<32} {counts.get(seg, 0):>2}")
