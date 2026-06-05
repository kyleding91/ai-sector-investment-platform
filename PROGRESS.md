# PROGRESS — autonomous dev changelog

Newest first. One entry per completed feature. Each entry: date · branch · what
shipped · how it was verified. Review a feature with `git diff main..<branch>` or
`git checkout <branch>` then open http://127.0.0.1:8010/.

> PRs will be opened once `gh` is installed + a GitHub remote is added. Until then
> features live on local branches listed here.

---

## 2026-06-05 · `feat/tests-ci` · pytest smoke tests + ruff config
Added offline test + lint scaffolding (no network, no `ANTHROPIC_API_KEY`). New `pyproject.toml`
configures **ruff** (E/F/W/I/B/UP; `E501` ignored — `companies.py` is a hand-aligned table) and
**pytest** (`testpaths=tests`, quiet warnings). New `tests/` holds **23 tests**: `test_api.py`
drives FastAPI's `TestClient` over health/companies/returns/metrics/filings/filing-insights with
shape assertions always-on and data-dependent ones auto-skipped on an empty DB (`/api/snapshot`
only hit with a bogus ticker so it stays offline); `test_extraction.py` covers `html_to_text`,
`short_name`, `extract_mgmt_quotes`, `extract_self_description`, `_cagr`, `_fy_label`;
`test_returns.py` checks `_ann_return` geometric math + insufficient-history `None`.
`requirements-dev.txt` pins pytest + httpx + ruff. A few minor lint fixes make the tree clean:
dropped an unused import (`filings.py`), modernized `typing.Iterable`→`collections.abc` (`edgar.py`),
renamed an ambiguous `l` (`main.py`) and an unused loop var (`etl.py`).
**Verified:** `ruff check .` → "All checks passed!"; `pytest` → **23 passed in 0.25s** (verbose run
confirms none skipped against the populated DB).
**Review:** `git diff main..feat/tests-ci` · `pip install -r requirements-dev.txt && pytest && ruff check .`

## 2026-06-05 · `feat/watchlist-admin` · Add/remove/re-bucket companies from the UI
New `backend/watchlist.py` mutates the **live `companies` table** so the heat map can be curated
without hand-editing `backend/companies.py` (edits persist in SQLite until the next
`python -m backend.seed`). Adding a company also pulls ~11Y of prices via the ETL so the new row
appears immediately. Endpoints in `main.py`: `GET /api/segments`, `POST /api/watchlist` (add +
validate segment/dupes + pull prices), `PATCH /api/watchlist/{ticker}` (re-bucket → updates layer),
`DELETE /api/watchlist/{ticker}` (remove company + prices/metrics/facts). `_company_meta` now reads
the DB (not the seed list) so runtime-added tickers resolve for `/api/snapshot`; CORS broadened to
allow POST/PATCH/DELETE. `frontend/index.html` gains a **"⚙ Manage watchlist"** modal — an add form
(ticker/yahoo/name/segment dropdown/notes) plus a current-watchlist list with per-row re-bucket
`<select>` and Remove button; benchmarks (SOX/SP500TR) are hidden + server-protected. CLI:
`python -m backend.watchlist list|add|rebucket|remove`.
**Verified** end-to-end on :8010 — added **QCOM** via `POST /api/watchlist` → **2766 price rows**
pulled, appeared in `/api/returns` (layer 7) and `/api/snapshot` (DB-backed meta); duplicate add and
bad segment → 400; `PATCH` re-bucketed QCOM to layer 1; `DELETE SOX` → 400 (protected); `DELETE QCOM`
removed 2766 prices + the row, returns back to 38. Browser (Claude Preview): modal opens, 9 segment
options, 38 mutable rows (benchmarks excluded), screenshot confirms layout. DB left clean.
**Review:** `git diff main..feat/watchlist-admin` · open http://127.0.0.1:8010/ → "Manage watchlist".

## 2026-06-05 · `feat/heatmap-ux` · Heat-map horizon focus toggle (re-colors without reload)
Added a **"Focus horizon"** selector to the heat-map controls in `frontend/index.html`. Picking a
horizon (1Y/3Y/5Y/10Y) emphasizes that single return column — a ring on each value cell plus a
tinted header — and **dims** the other columns (white cells, muted text) so the eye lands on one
time frame; "All" restores the full diverging view. Focusing a horizon also **auto-syncs "Sort
within layer"** to that horizon, so rows reorder instantly. New `render()` `focus` param drives a
per-horizon `hClass()` applied to header, benchmark-row, and company cells; the `focus-horizon`
change listener sets sort + re-renders. Entirely client-side — no reload, no refetch.
**Verified:** in a real browser via Claude Preview on :8010 — toggling to "3Y" flips `sort-by` to
`3y` and re-paints **41 focus-col + 123 dimmed** cells with zero network calls; "All" restores.
Returns endpoint serves 38 rows; page 200s. No backend changes.
**Review:** `git diff main..feat/heatmap-ux` · open http://127.0.0.1:8010/ and toggle Focus horizon.

## 2026-06-05 · `feat/metric-sparklines` · Fundamentals trend sparklines on the deep-dive
Added `get_series()` to `backend/metrics.py` — builds a per-year **revenue + gross/operating
margin** series (oldest→newest, capped at the latest 10 FYs) from the merged multi-source
`fundamentals`, computing margins per year and reporting in the statement currency. Exposed via
new `GET /api/fundamentals/{ticker}` (`exists:false` when no facts). `frontend/panel.js` gains a
reusable `sparklineSVG()` and a **"Fundamentals trend"** block — three compact inline sparklines
(revenue, gross margin, operating margin) with the latest value and FY span; `index.html` +
`stock.html` fetch it alongside the other panels. The block self-hides when a ticker has < 2 data
points (non-filers, recent IPOs).
**Verified:** `/api/fundamentals/NVDA` → FY2017→FY2026 with revenue + margins; TSM in **TWD** (10 pts),
Samsung `005930` in **KRW** (4 pts, yfinance-only), unknown ticker → `exists:false`. panel.js
syntax-clean (node --check); server restarted on :8010 to pick up the new module.
**Review:** `git diff main..feat/metric-sparklines` · `curl :8010/api/fundamentals/NVDA` · open the NVDA deep-dive.

## 2026-06-05 · `feat/risk-insights` · Extractive "key risks in their own words"
Added `extract_risk_factors()` to `backend/filing_insights.py` — pulls **3-5 verbatim risk-factor
headlines** from each company's latest annual report (Item 1A in 10-Ks, Item 3.D in 20-Fs), no LLM.
Two strategies: (1) the company's own **risk-summary list** — `Risk Factors Summary` bullets (NVDA,
AMD, MU…) or the `Overview of risk factors` table (ASML, ARM, GFS) — preferred when it yields ≥3;
(2) a **section-body fallback** that harvests the bold risk sub-headings (short sentence-paragraph
followed by a long explanatory paragraph) for filers without a summary (TXN, AMAT, CSCO, MRVL, ADI,
UMC). Filters category headers, page chrome, risk-type column labels, and boilerplate. New
`risk_factors` field flows through `content_json` → `/api/filing-insights/{ticker}`; panel.js renders
an amber **"Key risks, in their words"** block linking back to the filing on EDGAR.
**Verified:** full run = 36 filers; **NVDA & ASML each show 5 verbatim risk bullets** with working
EDGAR links; 30/36 yield 5 risks, the rest (INTC/TSM/LRCX/DELL/AMZN) degrade gracefully to none.
`/api/filing-insights/NVDA` & `/ASML` return `risk_factors` + `source_url`; panel.js syntax-clean.
**Review:** `git diff main..feat/risk-insights` · `python -m backend.filing_insights show NVDA` · open NVDA deep-dive.

## 2026-06-05 · `feat/fx-normalize` · USD normalization for foreign filers
Added `backend/fx.py` — fetches **spot FX rates** (`XXXUSD=X` via yfinance) into a new
`fx_rates` table, and an idempotent `db.py` column migration so `company_metrics` carries
`revenue_latest_usd` / `fx_rate` / `fx_rate_asof`. `metrics.py` converts non-USD revenue
(TWD/KRW/EUR) at the stored rate; the deep-dive panel shows **native + ≈USD** with a caveat that
the USD figure is a spot conversion (not a fiscal-year-average reported number). Margins/CAGRs
stay currency-neutral. CLI: `python -m backend.fx refresh|show`.
**Verified:** rates EUR 1.162, KRW 0.000647, TWD 0.03176 (2026-06-05); conversions check out —
TSM NT$3.81T → ≈$121.0B, ASML €32.7B → ≈$38.0B, Samsung ₩333.6T → ≈$215.9B, NVDA USD identity.
`/api/metrics/TSM` returns native + usd + fx_rate + fx_rate_asof; panel.js syntax-clean.
**Review:** `git diff main..feat/fx-normalize` · `python -m backend.fx show` · open TSM deep-dive.

## 2026-06-04 · `feat/xbrl-fundamentals` · EDGAR XBRL fundamentals (authoritative 2nd source)
Added `backend/xbrl.py` — pulls the SEC **`companyfacts`** API into the shared `fundamentals`
table tagged `source='edgar_xbrl'`, which `metrics.py` already prioritizes over yfinance. Maps
us-gaap concepts (Revenue/NetIncome/OperatingIncome/GrossProfit/Diluted+BasicEPS), **unions tags
by era** (NVDA revenue spans `Revenues` + `RevenueFromContractWith…`), maps foreign 20-F filers
via the **`ifrs-full`** taxonomy in their statement currency (TSM/UMC in TWD, GFS in USD), and
**snaps** XBRL fiscal-year ends to yfinance's month-end convention so the two sources dedupe.
`metrics.py` now computes a real `sources` label (`SEC EDGAR XBRL`, `… + yfinance`) shown on the
deep-dive panel. CLI: `python -m backend.xbrl refresh|show|compare`.
**Verified:** NVDA/TSM/UMC/GFS XBRL revenue matches yfinance to +0.000%; full run = 2783 rows,
36 SEC filers on XBRL, Samsung/SK hynix stay yfinance-only (no CIK). `/api/metrics/NVDA`→
`SEC EDGAR XBRL`, `/api/metrics/TSM`→`SEC EDGAR XBRL + yfinance` (TWD), `005930`→`yfinance` (KRW).
**Review:** `git diff main..feat/xbrl-fundamentals` · `python -m backend.xbrl compare NVDA`.

## 2026-06-04 · `feat/filing-search` · Full-text filing search
Added `backend/search.py` — a SQLite **FTS5** index over the on-disk filings corpus
(primary docs + earnings/supplement/presentation exhibits), with bm25-ranked,
highlighted snippets linking back to SEC EDGAR. New `/api/search?q=&limit=&ticker=`
endpoint (sanitized queries — can't 500; empty/no-index → no results). Dashboard gains
a debounced search box that highlights matches and opens the company deep-dive on click.
CLI: `python -m backend.search index|search|status`.
**Verified:** indexed 1408 docs; `HBM`→MU, `foundry capacity`→INTC, ticker-filtered
NVDA, special-char query all correct (200, no crash); search box present on `/`.
**Review:** `git diff main..feat/filing-search` · run server on :8010, search "HBM".
