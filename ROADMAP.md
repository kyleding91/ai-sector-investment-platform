# ROADMAP — autonomous development backlog

This is the shared backlog the self-paced dev loop reads each cycle. Pick the
**top unstarted, unblocked** item, build it on a feature branch, verify, commit,
log to `PROGRESS.md`, then return to `main`.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done (see PROGRESS.md) · `[blocked]`

## Constraints (always honor)
- Port **8010** only. Never touch the `:8000` process (separate FS app).
- `ANTHROPIC_API_KEY` is **absent** → no live-LLM features can be verified; defer them.
- Reuse the existing stack: FastAPI + SQLite + vanilla HTML/JS (no build step).
- Do not modify the reference codebase at `/Users/xuehui/FS Investing Platform`.
- One feature per branch (`feat/<slug>`). Verify before committing.

## Priority backlog (offline-friendly first)

1. `[x]` **Full-text filing search** (`feat/filing-search`) — see PROGRESS.md
   - Index the on-disk `filings/` corpus (SQLite FTS5) and add `/api/search?q=`.
   - Frontend: a search box returning snippet + ticker + link to the SEC EDGAR doc.
   - Acceptance: query "HBM" returns MU/SK-relevant hits with working EDGAR links.

2. `[x]` **EDGAR XBRL fundamentals (second source)** (`feat/xbrl-fundamentals`) — see PROGRESS.md
   - Pull SEC `companyfacts` API into `fundamentals` tagged `source='edgar_xbrl'` alongside yfinance.
   - Acceptance: NVDA revenue from XBRL matches yfinance within rounding; UI shows source.

3. `[x]` **FX normalization for foreign filers** (`feat/fx-normalize`) — see PROGRESS.md
   - Convert TWD/KRW/EUR revenue to USD using a stored FX rate; show both.
   - Acceptance: TSM revenue shows USD + TWD; caveat text updated.

4. `[x]` **Extractive "key risks in their own words"** (`feat/risk-insights`) — see PROGRESS.md
   - New extractive block from Item 1A (10-K) / Item 3.D (20-F), like filing_insights.
   - Acceptance: NVDA/ASML show 3-5 verbatim risk bullets linking to EDGAR.

5. `[x]` **Fundamentals sparklines on deep-dive** (`feat/metric-sparklines`) — see PROGRESS.md
   - Small revenue/margin trend charts on stock.html from `fundamentals`.
   - Acceptance: NVDA shows a multi-year revenue sparkline.

6. `[x]` **Heat map UX: horizon toggle + sorting** (`feat/heatmap-ux`) — see PROGRESS.md
   - Toggle return horizon (1Y/3Y/5Y) and sort within layers.
   - Acceptance: toggling re-colors without reload; sort works.

7. `[x]` **Watchlist management UI** (`feat/watchlist-admin`) — see PROGRESS.md
   - Add/remove/re-bucket a company without hand-editing companies.py.
   - Acceptance: adding a ticker seeds + pulls prices via the UI.

8. `[~]` **Test + lint scaffolding** (`feat/tests-ci`)
   - pytest smoke tests for endpoints + extraction; ruff config.
   - Acceptance: `pytest` green; `ruff check` clean.

9. `[blocked]` **Live Claude deep-dive insights** — needs `ANTHROPIC_API_KEY`.

Add new ideas to the bottom; re-prioritize by editing order.
