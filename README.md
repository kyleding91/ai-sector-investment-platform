# AI Sector Investment Platform

A local web dashboard for tracking AI-infrastructure and semiconductor stocks, organized
as a **value chain** — from chip design at the top to the hyperscalers that buy the compute
at the bottom. Heat map of annualized total returns colored against the PHLX Semiconductor
Index (SOX), plus per-stock price/performance snapshots.

> Personal research tool, runs locally only. Not investment advice.

## The value chain (9 layers)

| Layer | Segment | Examples |
|---|---|---|
| 1 | AI Accelerators / Compute | NVDA, AMD, INTC |
| 2 | EDA & Chip IP | SNPS, CDNS, ARM |
| 3 | Semiconductor Equipment | ASML, AMAT, LRCX, KLAC, TER |
| 4 | Foundry & Manufacturing | TSM, GFS, UMC |
| 5 | Memory (DRAM/HBM/NAND) | MU, Samsung (005930), SK hynix (000660) |
| 6 | Networking & Interconnect | AVGO, MRVL, ANET, CSCO, ALAB, CRDO |
| 7 | Analog, Power & Connectivity | MPWR, ADI, TXN, NXPI, ON |
| 8 | Data Center Systems & Infra | SMCI, DELL, VRT, CRWV, NBIS |
| 9 | Hyperscalers / AI Demand | MSFT, GOOGL, AMZN, META, ORCL |

The watchlist lives in [`backend/companies.py`](backend/companies.py) — edit there and rerun
`seed` to add or re-bucket a company. Each company gets one primary segment; cross-cutting
names (AVGO, INTC, VRT) carry a note.

## Quick start

```bash
pip install -r requirements.txt

python -m backend.seed                    # companies.py -> SQLite
python -m backend.etl refresh             # ~11Y of daily closes from Yahoo (~1 min)
python -m backend.fundamentals refresh all  # income-statement facts from yfinance
python -m backend.xbrl refresh all          # authoritative SEC EDGAR XBRL facts (supersede yfinance)
python -m backend.fx refresh                # spot FX rates (TWD/KRW/EUR → USD) for foreign filers
python -m backend.metrics refresh all     # derive revenue/margin/CAGR rollups (+ USD-normalized revenue)
python -m scripts.seed_insights           # hand-seeded illustrative deep-dive panels

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
# open http://127.0.0.1:8010/
```

Runs on port **8010** (8000 is reserved for the separate FS platform). For ongoing use, only
`etl refresh` (when prices are stale) + running the server.

### Stock deep-dive insights (Phase 3)

Insight panels are Claude-generated and schema-constrained (`StockPanel` Pydantic model). Live
generation needs `ANTHROPIC_API_KEY`:

```bash
export ANTHROPIC_API_KEY=sk-...
python -m backend.insights refresh stock NVDA   # one ticker
python -m backend.insights refresh all          # whole watchlist
python -m backend.insights show stock NVDA
```

Without a key, 6 hand-seeded illustrative panels (NVDA, ASML, TSM, MU, AVGO, MSFT) ship via
`python -m scripts.seed_insights` so the UI works offline. They are clearly labelled
illustrative — verify before acting, not investment advice.

### SEC filings (Phase 4)

Downloads the EDGAR filing corpus into `filings/<TICKER>/<TYPE>/<period>/`. The form set is
data-driven — US domestic filers yield **10-K / 10-Q / 8-K-earnings**, while foreign private
issuers (TSM, ASML, UMC, GFS, ARM, NBIS) yield **20-F / 6-K**. Samsung and SK hynix don't file
with the SEC and are skipped automatically.

```bash
python -m backend.filings refresh all          # whole watchlist (~3 years)
python -m backend.filings refresh NVDA         # one ticker
python -m backend.filings refresh "Foundry & Manufacturing"   # a value-chain segment
python -m backend.filings verify NVDA          # re-check on-disk files vs DB (sha + markers)
python -m backend.filings status               # summary table of ok/failed
```

Each filing is downloaded with the SEC-required User-Agent, throttled under 10 req/s, validated
(size + form-type markers), written atomically with a `meta.json`, and recorded in the `filings`
table. 8-K earnings additionally pull the press-release / supplement exhibits; 6-K cover pages
pull their largest content exhibit. Runs are resumable — already-downloaded filings are skipped
by sha, and transient network errors retry with backoff.

### "In their own words" — filing insights (Phase 4b)

Extractive, **no-LLM** insights pulled verbatim from each company's latest annual report and
earnings release on disk — how the company describes its own business, in its own words. Every
sentence is sourced text that links back to the filing on SEC EDGAR; nothing is AI-generated, so
this works offline with no API key. Surfaces on the deep-dive page as an "In their own words"
block: a bold self-description lead, a business overview, reportable-segments note, strategy
points, and management quotes from the latest earnings release.

```bash
python -m backend.filing_insights refresh all     # whole watchlist (parses on-disk filings)
python -m backend.filing_insights refresh NVDA     # one ticker
python -m backend.filing_insights show NVDA        # print extracted payload
python -m backend.filings backfill                 # re-fetch missing 8-K/6-K exhibits (enables quotes)
```

Extraction is form-aware (10-K Item 1 Business → Item 1A; 20-F Item 4 → Item 5) with a
definitional-anchor fallback for cross-referenced or integrated-annual-report filings, plus
boilerplate/false-positive guards (legal, forward-looking, IR, incorporation language). Results
are cached in the `filing_insights` table. Samsung / SK hynix have no SEC filings, so they have no
block.

### Full-text filing search (Phase 5)

Keyword search across the entire downloaded SEC filings corpus — primary documents plus
earnings-release / supplement / presentation exhibits — backed by a local SQLite **FTS5**
index. No LLM and no network at query time; every hit links back to the document on SEC
EDGAR. A search box on the dashboard returns highlighted snippets; clicking a hit opens that
company's deep-dive panel.

```bash
python -m backend.search index            # (re)build the FTS index from filings/
python -m backend.search search "HBM"      # query from the CLI
python -m backend.search status            # doc count + last-indexed time
# API: GET /api/search?q=HBM&limit=25[&ticker=MU]
```

Rebuild the index after `python -m backend.filings refresh` pulls new filings.

### EDGAR XBRL fundamentals — authoritative second source (Phase 6)

yfinance gives a fast income statement, but the authoritative numbers live in each
filer's XBRL data at SEC EDGAR. `backend/xbrl.py` pulls the `companyfacts` API and writes
the same canonical concepts (Revenue, NetIncome, OperatingIncome, GrossProfit, Diluted/Basic
EPS) into the shared `fundamentals` table tagged `source='edgar_xbrl'`. `metrics.py` already
prioritizes `edgar_xbrl` over `yfinance` for the same fiscal period, so XBRL transparently
supersedes yfinance and the deep-dive panel's "source:" line reflects what was actually used
(e.g. `SEC EDGAR XBRL` or `SEC EDGAR XBRL + yfinance`).

```bash
python -m backend.xbrl refresh all          # whole watchlist
python -m backend.xbrl refresh NVDA         # one ticker
python -m backend.xbrl compare NVDA         # XBRL vs yfinance, side-by-side with % delta
python -m backend.xbrl show NVDA            # print stored edgar_xbrl facts
```

Coverage notes: a concept's history can be split across us-gaap tags by era (NVDA reports
revenue under both `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax`) — the
tags are **unioned** so coverage is complete. Foreign private issuers file 20-F under the IFRS
taxonomy (`ifrs-full`), so TSM/UMC/GFS are mapped there and reported in their statement currency
(TSM/UMC in TWD, GFS in USD) to match the existing yfinance rows. yfinance's month-end
fiscal-year convention is matched by snapping each XBRL end-date to the nearest stored period.
Samsung (005930) and SK hynix (000660) don't file with the SEC and stay yfinance-only.

## Architecture

Local SQLite (`data/ai_stocks.db`) holds company metadata, daily adjusted closes, raw
income-statement facts (`fundamentals`), derived rollups (`company_metrics`), generated
insight panels (`insights`), and an index of downloaded SEC filings (`filings`; the documents
themselves live on disk under `filings/`). A FastAPI server (`backend/main.py`) reads from SQLite and makes
ad-hoc yfinance calls for live snapshots (market cap / P/E / div yield). The frontend is static
HTML/JS (no build step): `index.html` (heat map) and `stock.html` (standalone page) share
`panel.js`. Every refresh is manual via CLI — no scheduler.

Fundamentals flow in two stages: `fundamentals.py` pulls raw facts (revenue, net/operating
income, gross profit, EPS) tagged with their statement currency, then `metrics.py` derives
revenue/margin/CAGR and a per-company key metric into `company_metrics`. `insights.py` calls
Claude with web search to produce schema-validated `StockPanel` deep dives.

```
backend/   companies.py · db.py · seed.py · etl.py · returns.py · fundamentals.py ·
           xbrl.py · fx.py · metrics.py · insights.py · edgar.py · filings.py · filing_insights.py · main.py
frontend/  index.html · stock.html · panel.js
scripts/   seed_insights.py   (hand-seeded illustrative panels)
data/      ai_stocks.db   (gitignored)
filings/   <TICKER>/<TYPE>/<period>/  downloaded SEC filings (gitignored)
```

### API endpoints (Phase 2/3/4)

| Endpoint | Returns |
|---|---|
| `/api/metrics/{ticker}` | derived revenue/margin/CAGR + `financial_currency`, or `{exists:false}` |
| `/api/insights/stock/{ticker}` | latest `StockPanel` insight, or `{exists:false}` |
| `/api/filings/{ticker}` | list of downloaded SEC filings (newest-first), or empty for non-filers |
| `/api/filing-insights/{ticker}` | extractive "in their own words" insights from on-disk filings, or `{exists:false}` |

## Data sources & freshness

| Field | Source | Refresh |
|---|---|---|
| Daily adjusted closes (heat map, return math, 5Y chart) | yfinance → local `prices` | `python -m backend.etl refresh` |
| Market cap / P/E / dividend yield | live yfinance call per `/api/snapshot/{ticker}` | live (Yahoo retail tier 15+ min delayed) |
| Income-statement facts (revenue, margins, EPS) | yfinance `financials` → local `fundamentals` (`source='yfinance'`) | `python -m backend.fundamentals refresh all` |
| Income-statement facts (authoritative second source) | SEC EDGAR XBRL `companyfacts` → `fundamentals` (`source='edgar_xbrl'`) | `python -m backend.xbrl refresh all` |
| Spot FX rates (foreign-filer USD normalization) | yfinance `XXXUSD=X` → local `fx_rates` | `python -m backend.fx refresh` |
| Derived metrics (margin, CAGR, key metric) | computed from `fundamentals` (XBRL preferred over yfinance) → `company_metrics` | `python -m backend.metrics refresh all` |
| Stock deep-dive insight panels | Claude API (web search) → `insights` | `python -m backend.insights refresh all` |
| SEC filings (10-K/10-Q/8-K, 20-F/6-K) | SEC EDGAR → `filings/` + `filings` table | `python -m backend.filings refresh all` |

Nothing auto-updates. The "last refreshed" timestamp shows in the heat-map header.

## Notes & caveats

- **Foreign rows:** Samsung (`005930.KS`) and SK hynix (`000660.KS`) are priced in **KRW**, so
  their returns are local-currency total returns. A USD investor's realized return also moves
  with FX — not reflected here.
- **Benchmark mismatch:** the SOX index is price-only while individual stocks use
  dividend-adjusted (total-return) closes. Semis pay small dividends, so the bias is minor, but
  comparisons are not strictly apples-to-apples.
- **Recent IPOs** (CRWV, NBIS, ALAB, ARM, GFS, CRDO) show N/A for longer horizons.
- **Foreign-filer revenue currency:** financials are reported in the statement currency, not
  USD — TSM/UMC in **TWD**, Samsung/SK hynix in **KRW**, ASML in **EUR**. Absolute revenue is
  tagged with `financial_currency`, and the deep-dive panel now also shows a **USD-normalized**
  figure (`revenue_latest_usd`) converted at a stored **spot** rate from `backend.fx` (e.g. TSM
  shows NT$ and ≈ US$). Because it's a spot rate rather than a fiscal-year average, the USD number
  is a comparison aid, not a reported figure — the UI says so. Margins and CAGRs are
  currency-neutral ratios so they compare cleanly across filers regardless of currency.

## Roadmap

- Phase 2 — fundamentals + derived metrics (revenue/margin/CAGR + a per-company key metric
  like NVDA data-center revenue, TSM ≤5nm mix, MU HBM ramp, hyperscaler capex). **Shipped.**
- Phase 3 — Claude-generated stock deep-dive panels (schema-constrained, AI-specific drivers).
  **Shipped** (live generation needs `ANTHROPIC_API_KEY`; 6 illustrative panels ship hand-seeded).
- Phase 4 — SEC filings corpus (10-K / 10-Q / 8-K for domestic filers; 20-F / 6-K for foreign
  private issuers). **Shipped** — `python -m backend.filings refresh all`. Samsung/SK hynix are
  non-SEC filers and skipped.
- Phase 4b — extractive "in their own words" filing insights (no LLM; verbatim self-description,
  business overview, segments, strategy, and management quotes sourced from on-disk filings).
  **Shipped** — `python -m backend.filing_insights refresh all`.
