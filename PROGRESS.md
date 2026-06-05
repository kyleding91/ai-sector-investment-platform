# PROGRESS — autonomous dev changelog

Newest first. One entry per completed feature. Each entry: date · branch · what
shipped · how it was verified. Review a feature with `git diff main..<branch>` or
`git checkout <branch>` then open http://127.0.0.1:8010/.

> PRs will be opened once `gh` is installed + a GitHub remote is added. Until then
> features live on local branches listed here.

---

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
