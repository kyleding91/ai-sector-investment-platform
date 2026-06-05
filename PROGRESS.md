# PROGRESS — autonomous dev changelog

Newest first. One entry per completed feature. Each entry: date · branch · what
shipped · how it was verified. Review a feature with `git diff main..<branch>` or
`git checkout <branch>` then open http://127.0.0.1:8010/.

> PRs will be opened once `gh` is installed + a GitHub remote is added. Until then
> features live on local branches listed here.

---

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
