# PROGRESS — autonomous dev changelog

Newest first. One entry per completed feature. Each entry: date · branch · what
shipped · how it was verified. Review a feature with `git diff main..<branch>` or
`git checkout <branch>` then open http://127.0.0.1:8010/.

> PRs will be opened once `gh` is installed + a GitHub remote is added. Until then
> features live on local branches listed here.

---

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
