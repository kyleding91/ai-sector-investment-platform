"""Full-text search over the on-disk SEC filings corpus.

Builds a SQLite FTS5 index from the downloaded filings (primary documents plus
earnings-release / supplement / presentation exhibits) and answers keyword
queries with highlighted snippets that link back to the document on SEC EDGAR.

No LLM, no network at query time — everything is local full-text search.

CLI
---
    python -m backend.search index            # (re)build the FTS index from filings/
    python -m backend.search search "HBM"      # query and print top hits
    python -m backend.search status            # row count + last-indexed time
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.db import connect, get_meta, set_meta
from backend.filing_insights import html_to_text

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Exhibit kinds worth indexing (management commentary lives here); other exhibits
# (XBRL R-files, index-headers, cover pages) are noise for search.
_EXHIBIT_KINDS = {"press_release", "supplement", "content", "presentation"}
_MAX_DOC_CHARS = 600_000  # guard against a pathological single document


def _create_index(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS filing_search")
    conn.execute(
        "CREATE VIRTUAL TABLE filing_search USING fts5("
        "ticker UNINDEXED, accession UNINDEXED, form UNINDEXED, "
        "period_end UNINDEXED, url UNINDEXED, doc_kind UNINDEXED, "
        "content, tokenize='porter unicode61')"
    )


def _docs_for_filing(local_path: str, filing_type: str, primary_url: str):
    """Yield (doc_kind, url, path) for the primary doc and substantive exhibits."""
    primary = PROJECT_ROOT / local_path
    if primary.exists():
        yield "primary", primary_url, primary
    # Earnings filings carry their narrative in exhibits listed in meta.json.
    folder = primary.parent
    meta_path = folder / "meta.json"
    if filing_type in ("8-K-earnings", "6-K") and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:  # noqa: BLE001
            meta = {}
        for ex in meta.get("exhibits", []):
            if ex.get("kind") not in _EXHIBIT_KINDS:
                continue
            p = folder / ex.get("filename", "")
            if p.exists():
                yield ex.get("kind"), ex.get("url", primary_url), p


def build_index() -> int:
    """(Re)build the FTS index from all status='ok' filings. Returns doc count."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, accession, filing_type, form, period_end, "
            "primary_doc_url, local_path FROM filings WHERE status='ok' "
            "ORDER BY ticker, filed_at DESC"
        ).fetchall()
        _create_index(conn)
        n_docs = 0
        for ticker, accession, ftype, form, period, purl, lpath in rows:
            for kind, url, path in _docs_for_filing(lpath, ftype, purl):
                try:
                    text = html_to_text(path.read_bytes())
                except Exception as e:  # noqa: BLE001
                    print(f"  [skip] {ticker} {accession} {path.name}: {e}")
                    continue
                if not text or len(text) < 40:
                    continue
                conn.execute(
                    "INSERT INTO filing_search(ticker, accession, form, "
                    "period_end, url, doc_kind, content) VALUES (?,?,?,?,?,?,?)",
                    (ticker, accession, form, period, url, kind, text[:_MAX_DOC_CHARS]),
                )
                n_docs += 1
        conn.commit()
    set_meta("filing_search_indexed_at", datetime.now(timezone.utc).isoformat())
    set_meta("filing_search_doc_count", str(n_docs))
    return n_docs


def _fts_query(q: str) -> str:
    """Sanitize free text into a safe FTS5 MATCH expression (AND of terms)."""
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*", q or "")
    return " ".join(f'"{t}"' for t in toks)


def search(q: str, limit: int = 25, ticker: str | None = None) -> list[dict]:
    """Return ranked hits with highlighted snippets. Empty query → []."""
    match = _fts_query(q)
    if not match:
        return []
    sql = (
        "SELECT ticker, form, period_end, url, doc_kind, "
        "snippet(filing_search, 6, '<mark>', '</mark>', '…', 14) AS snip "
        "FROM filing_search WHERE filing_search MATCH ?"
    )
    params: list = [match]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY rank LIMIT ?"
    params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "ticker": r[0],
            "form": r[1],
            "period_end": r[2],
            "url": r[3],
            "doc_kind": r[4],
            "snippet": r[5],
        }
        for r in rows
    ]


def index_exists() -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='filing_search'"
        ).fetchone()
    return row is not None


def _cli(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "index":
        print("Building full-text index from filings/ …")
        n = build_index()
        print(f"Indexed {n} documents.")
        return 0
    if cmd == "search":
        if len(argv) < 2:
            print("usage: python -m backend.search search \"<query>\"")
            return 2
        q = " ".join(argv[1:])
        hits = search(q)
        if not hits:
            print(f"No hits for {q!r}.")
            return 0
        print(f"{len(hits)} hit(s) for {q!r}:\n")
        for h in hits:
            snip = re.sub(r"</?mark>", "*", h["snippet"])
            print(f"  {h['ticker']:6} {h['form']:13} {h['period_end']}  [{h['doc_kind']}]")
            print(f"         {snip}")
            print(f"         {h['url']}\n")
        return 0
    if cmd == "status":
        if not index_exists():
            print("No index yet — run: python -m backend.search index")
            return 0
        with connect() as conn:
            n = conn.execute("SELECT count(*) FROM filing_search").fetchone()[0]
        print(f"filing_search: {n} documents")
        print(f"last indexed:  {get_meta('filing_search_indexed_at') or '—'}")
        return 0
    print(f"Unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
