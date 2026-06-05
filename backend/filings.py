"""SEC EDGAR filing downloader / validator / store for the AI watchlist.

The form set is data-driven (see edgar.ALL_FORMS): US domestic filers yield
10-K / 10-Q / 8-K-earnings; foreign private issuers (TSM, ASML, UMC, GFS, ARM,
NBIS) yield 20-F / 6-K. Samsung (005930) and SK hynix (000660) don't file with
the SEC and are skipped with a clear message.

Pipeline per filing:
    1. Plan (list_filings, classify, filter 8-K to Item 2.02, cap 6-K noise)
    2. Download primary doc (+ earnings exhibits for 8-K, + largest content
       exhibit for 6-K cover pages)
    3. Validate (size + form-type markers)
    4. Persist atomically (.tmp -> rename) + a meta.json, record in SQLite
    5. On validation failure: retry once

Layout:  filings/<TICKER>/<filing_type>/<label>/<files>
         label = period_end for 10-K/10-Q/20-F/40-F, else filed_at_<acc-tail>

CLI:
    python -m backend.filings refresh NVDA          # one ticker
    python -m backend.filings refresh "Foundry & Manufacturing"   # a segment
    python -m backend.filings refresh all           # everything in companies.py
    python -m backend.filings refresh NVDA --force   # ignore cache
    python -m backend.filings verify NVDA           # re-check on-disk vs DB
    python -m backend.filings status                # summary table
"""
from __future__ import annotations

import hashlib
import json
import re as _re_module
import sys
from dataclasses import dataclass, field
from pathlib import Path

from backend.companies import BENCHMARK_TICKERS, COMPANIES, SEGMENTS
from backend.db import connect, init_schema
from backend.edgar import (
    Filing,
    archive_url,
    download,
    filter_8k_earnings,
    find_earnings_exhibits,
    get_ticker_cik_map,
    list_archive_files,
    list_filings,
)

import time as _time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILINGS_DIR = PROJECT_ROOT / "filings"
SINCE_DATE = "2023-01-01"   # past 3+ years, consistent with the fundamentals window
MAX_6K = 16                 # FPIs file frequent monthly-sales 6-Ks; keep the most recent

# Min sizes (bytes) for sanity. Inline-XBRL annual reports can hit 30+ MB.
MIN_SIZE = {
    "10-K": 100_000, "10-Q": 50_000, "8-K-earnings": 5_000,
    "20-F": 100_000, "40-F": 100_000, "6-K": 1_000,
}

# raw SEC form -> our filing_type label
FORM_TO_TYPE = {
    "10-K": "10-K", "10-Q": "10-Q", "20-F": "20-F", "40-F": "40-F", "6-K": "6-K",
}
ANNUAL_QUARTERLY = {"10-K", "10-Q", "20-F", "40-F"}


@dataclass
class ExhibitPlan:
    kind: str       # 'press_release' | 'supplement' | 'presentation' | 'content'
    filename: str
    url: str


@dataclass
class PlannedDownload:
    ticker: str
    filing_type: str          # '10-K' | '10-Q' | '8-K-earnings' | '20-F' | '6-K'
    form: str                 # raw SEC form
    period_end: str
    filed_at: str
    accession: str
    primary_doc_url: str
    primary_filename: str
    exhibits: list[ExhibitPlan] = field(default_factory=list)

    def label(self) -> str:
        if self.filing_type in ANNUAL_QUARTERLY:
            return self.period_end
        return f"{self.filed_at}_{self.accession[-6:]}"

    def folder(self) -> Path:
        return FILINGS_DIR / self.ticker / self.filing_type / self.label()


# ---------------- planning ----------------


def _largest_content_exhibit(f: Filing) -> ExhibitPlan | None:
    """For a 6-K whose primary doc is a thin cover page, grab the biggest
    *substantive* .htm/.pdf exhibit (usually the press release / financials).

    Skips EDGAR-generated junk (index-headers, the submission index, XBRL R-files)
    and only returns an exhibit that is meaningfully larger than the primary doc —
    otherwise the primary doc already is the substance."""
    items = list_archive_files(f)
    primary_lower = f.primary_document.lower()
    acc_nodash = f.accession.replace("-", "")

    def _is_junk(low: str) -> bool:
        return (
            "index" in low
            or acc_nodash in low
            or f.accession in low
            or _re_module.match(r"r\d+\.htm", low) is not None  # XBRL viewer fragments
        )

    primary_size = next(
        (int(it.get("size", 0) or 0) for it in items if it.get("name", "").lower() == primary_lower),
        0,
    )
    best = None
    best_size = -1
    for it in items:
        name = it.get("name", "")
        low = name.lower()
        if not low.endswith((".htm", ".html", ".pdf")) or low == primary_lower or _is_junk(low):
            continue
        size = int(it.get("size", 0) or 0)
        if size > best_size:
            best, best_size = name, size
    # Only worth a separate download if it's bigger than the primary and not tiny.
    if best is None or best_size <= max(primary_size, 20_000):
        return None
    return ExhibitPlan(kind="content", filename=best, url=archive_url(f.cik, f.accession, best))


def plan_for_ticker(ticker: str, cik: int) -> list[PlannedDownload]:
    raw = list_filings(ticker, cik, since=SINCE_DATE)
    plans: list[PlannedDownload] = []

    # Annual + quarterly (domestic 10-K/10-Q, FPI 20-F/40-F).
    for f in [x for x in raw if x.form in ("10-K", "10-Q", "20-F", "40-F")]:
        plans.append(PlannedDownload(
            ticker=f.ticker, filing_type=FORM_TO_TYPE[f.form], form=f.form,
            period_end=f.period_end, filed_at=f.filed_at, accession=f.accession,
            primary_doc_url=f.primary_doc_url,
            primary_filename=Path(f.primary_document).name,
        ))

    # 8-K earnings (domestic only — FPIs file no 8-K).
    for f in filter_8k_earnings(raw):
        exhibits = [
            ExhibitPlan(kind=kind, filename=name, url=archive_url(f.cik, f.accession, name))
            for name, kind in find_earnings_exhibits(f)
        ]
        plans.append(PlannedDownload(
            ticker=f.ticker, filing_type="8-K-earnings", form="8-K",
            period_end=f.period_end, filed_at=f.filed_at, accession=f.accession,
            primary_doc_url=f.primary_doc_url,
            primary_filename=Path(f.primary_document).name,
            exhibits=exhibits,
        ))

    # 6-K (FPI interim) — capped to the most recent MAX_6K, with the largest
    # content exhibit attached when the primary doc is a thin cover page.
    sixk = sorted([x for x in raw if x.form == "6-K"], key=lambda x: x.filed_at, reverse=True)
    for f in sixk[:MAX_6K]:
        exhibits: list[ExhibitPlan] = []
        ex = _largest_content_exhibit(f)
        if ex:
            exhibits.append(ex)
        plans.append(PlannedDownload(
            ticker=f.ticker, filing_type="6-K", form="6-K",
            period_end=f.period_end, filed_at=f.filed_at, accession=f.accession,
            primary_doc_url=f.primary_doc_url,
            primary_filename=Path(f.primary_document).name,
            exhibits=exhibits,
        ))

    return plans


# ---------------- validation ----------------


_TAG_RE = _re_module.compile(rb"<[^>]+>")
_ENTITY_RE = _re_module.compile(rb"&#?\w+;")
_WS_RE = _re_module.compile(rb"\s+")


def _normalize(body: bytes, max_bytes: int = 500_000) -> bytes:
    sample = body[:max_bytes]
    sample = _TAG_RE.sub(b" ", sample)
    sample = _ENTITY_RE.sub(b" ", sample)
    sample = _WS_RE.sub(b" ", sample)
    return sample.lower()


_MARKERS = {
    "10-K": ((b">10-K<", b">10-k<", b"Form 10-K", b"FORM 10-K", b"form 10-k"),
             (b"annual report", b"form 10-k")),
    "10-Q": ((b">10-Q<", b">10-q<", b"Form 10-Q", b"FORM 10-Q", b"form 10-q"),
             (b"quarterly report", b"form 10-q")),
    "20-F": ((b">20-F<", b">20-f<", b"Form 20-F", b"FORM 20-F", b"form 20-f"),
             (b"annual report", b"form 20-f")),
    "40-F": ((b">40-F<", b">40-f<", b"Form 40-F", b"FORM 40-F", b"form 40-f"),
             (b"annual report", b"form 40-f")),
    "8-K-earnings": ((b">8-K<", b">8-k<", b"Form 8-K", b"FORM 8-K", b"form 8-k"),
                     (b"form 8-k",)),
    "6-K": ((b">6-K<", b">6-k<", b"Form 6-K", b"FORM 6-K", b"form 6-k"),
            (b"form 6-k", b"report of foreign private issuer")),
}


def validate_content(filing_type: str, body: bytes, period_end: str) -> tuple[bool, str]:
    """Two-pass marker check: raw-bytes form marker, then HTML-normalized keywords."""
    min_size = MIN_SIZE.get(filing_type, 1024)
    if len(body) < min_size:
        return False, f"file too small ({len(body)} bytes; min {min_size})"

    markers = _MARKERS.get(filing_type)
    if markers is None:
        return False, f"unknown filing_type {filing_type!r}"
    raw_markers, norm_keywords = markers

    norm = None
    if not any(m in body for m in raw_markers):
        norm = _normalize(body)
        if not any(k in norm for k in norm_keywords):
            # 6-K cover pages are terse; accept on size alone (substance is in the exhibit).
            if filing_type == "6-K":
                return True, "ok (6-K marker not found verbatim — terse cover page)"
            return False, f"no form-type marker for {filing_type} in {len(body):,} bytes"

    if filing_type == "8-K-earnings":
        if norm is None:
            norm = _normalize(body)
        if not any(k in norm for k in (b"item 2.02", b"results of operations", b"earnings")):
            return False, "no earnings marker (Item 2.02 / Results of Operations / earnings)"

    if filing_type in ("10-K", "10-Q", "20-F"):
        if period_end and period_end.encode() not in body:
            return True, f"ok (period {period_end} not verbatim — may use prose date)"

    return True, "ok"


# ---------------- download + persist ----------------


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _persist_record(plan: PlannedDownload, *, status: str, msg: str,
                    local_path: str | None, size: int | None, sha: str | None) -> None:
    init_schema()
    with connect() as conn:
        conn.execute(
            "INSERT INTO filings(ticker, accession, filing_type, form, period_end, "
            "filed_at, primary_doc_url, local_path, size_bytes, sha256, status, validation_msg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, accession) DO UPDATE SET "
            "filing_type=excluded.filing_type, form=excluded.form, "
            "period_end=excluded.period_end, filed_at=excluded.filed_at, "
            "primary_doc_url=excluded.primary_doc_url, local_path=excluded.local_path, "
            "size_bytes=excluded.size_bytes, sha256=excluded.sha256, "
            "status=excluded.status, validation_msg=excluded.validation_msg",
            (
                plan.ticker, plan.accession, plan.filing_type, plan.form, plan.period_end,
                plan.filed_at, plan.primary_doc_url, local_path, size, sha, status, msg,
            ),
        )
        conn.commit()


def _existing_record(plan: PlannedDownload) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT status, sha256, local_path FROM filings WHERE ticker=? AND accession=?",
            (plan.ticker, plan.accession),
        ).fetchone()
    if not row:
        return None
    return {"status": row[0], "sha256": row[1], "local_path": row[2]}


def _write_meta(plan: PlannedDownload, primary_sha: str, primary_size: int,
                exhibits_meta: list[dict]) -> None:
    meta = {
        "ticker": plan.ticker,
        "filing_type": plan.filing_type,
        "form": plan.form,
        "period_end": plan.period_end,
        "filed_at": plan.filed_at,
        "accession": plan.accession,
        "primary_doc_url": plan.primary_doc_url,
        "primary_filename": plan.primary_filename,
        "primary_sha256": primary_sha,
        "primary_size_bytes": primary_size,
        "exhibits": exhibits_meta,
    }
    (plan.folder() / "meta.json").write_text(json.dumps(meta, indent=2))


def download_one(plan: PlannedDownload, *, force: bool = False) -> tuple[bool, str]:
    """Download + validate + persist. Returns (ok, message)."""
    existing = _existing_record(plan)
    if existing and existing["status"] == "ok" and not force:
        local = PROJECT_ROOT / existing["local_path"] if existing["local_path"] else None
        if local and local.exists() and _sha256(local.read_bytes()) == existing["sha256"]:
            return True, "cached"

    primary_path = plan.folder() / plan.primary_filename
    last_msg = ""
    for attempt in (1, 2):
        try:
            body = download(plan.primary_doc_url)
        except Exception as exc:  # noqa: BLE001
            last_msg = f"download error attempt {attempt}: {exc}"
            continue

        ok, reason = validate_content(plan.filing_type, body, plan.period_end)
        if not ok:
            last_msg = f"validate failed attempt {attempt}: {reason}"
            continue

        _atomic_write(primary_path, body)
        primary_sha = _sha256(body)
        primary_size = len(body)

        exhibits_meta: list[dict] = []
        exhibit_warnings: list[str] = []
        for ex in plan.exhibits:
            try:
                ebody = download(ex.url)
                if len(ebody) < 2_000:
                    exhibit_warnings.append(f"{ex.kind}({ex.filename}): too small ({len(ebody)})")
                    continue
                epath = plan.folder() / ex.filename
                _atomic_write(epath, ebody)
                exhibits_meta.append({
                    "kind": ex.kind, "filename": ex.filename, "url": ex.url,
                    "sha256": _sha256(ebody), "size_bytes": len(ebody),
                })
            except Exception as exc:  # noqa: BLE001
                exhibit_warnings.append(f"{ex.kind}({ex.filename}): {exc}")

        _write_meta(plan, primary_sha, primary_size, exhibits_meta)
        rel = str(primary_path.relative_to(PROJECT_ROOT))
        msg_parts = []
        if last_msg:
            msg_parts.append(last_msg)
        if exhibit_warnings:
            msg_parts.append("exhibit warnings: " + "; ".join(exhibit_warnings))
        msg = "ok" if not msg_parts else f"ok ({'; '.join(msg_parts)})"
        _persist_record(plan, status="ok", msg=msg, local_path=rel, size=primary_size, sha=primary_sha)
        return True, msg

    _persist_record(plan, status="failed", msg=last_msg, local_path=None, size=None, sha=None)
    return False, last_msg


# ---------------- orchestration ----------------


def _resolve_cik(ticker: str) -> int | None:
    """Resolve ticker -> CIK. Returns None for non-SEC filers (e.g. Korea listings)."""
    m = get_ticker_cik_map()
    t = ticker.upper()
    for cand in (t, t.replace(".", "-"), t.replace("-", ".")):
        if cand in m:
            return m[cand]
    return None


def refresh_ticker(ticker: str, *, force: bool = False) -> dict:
    cik = _resolve_cik(ticker)
    if cik is None:
        print(f"\n=== {ticker}: not an SEC filer (no CIK) — skipped ===")
        return {"ticker": ticker.upper(), "ok": 0, "failed": 0, "skipped": True}

    print(f"\n=== {ticker} (CIK {cik}) ===")
    try:
        plans = plan_for_ticker(ticker.upper(), cik)
    except Exception as exc:  # noqa: BLE001 — one ticker's network failure shouldn't abort the batch
        print(f"  PLANNING FAILED: {exc}")
        return {"ticker": ticker.upper(), "ok": 0, "failed": 0, "plan_error": str(exc)}
    plans.sort(key=lambda p: (p.filing_type, p.period_end, p.filed_at))
    print(f"  {len(plans)} filings to process")

    summary = {"ticker": ticker.upper(), "ok": 0, "failed": 0}
    for p in plans:
        ok, msg = download_one(p, force=force)
        flag = "OK " if ok else "FAIL"
        size_kb = ""
        if ok:
            pp = p.folder() / p.primary_filename
            if pp.exists():
                size_kb = f"  {pp.stat().st_size // 1024} KB"
        print(f"  [{flag}] {p.filing_type:<13} {p.label():<22} {p.primary_filename:<30}{size_kb}  {msg if not ok else ''}")
        summary["ok" if ok else "failed"] += 1
    print(f"  -> {summary['ok']} ok, {summary['failed']} failed")
    return summary


def refresh_segment(segment: str, *, force: bool = False) -> None:
    tickers = [c[0] for c in COMPANIES if c[3] == segment and c[0] not in BENCHMARK_TICKERS]
    if not tickers:
        raise SystemExit(f"No tickers in segment {segment!r}")
    print(f"Segment {segment!r}: {len(tickers)} tickers")
    totals = {"ok": 0, "failed": 0}
    for t in tickers:
        s = refresh_ticker(t, force=force)
        totals["ok"] += s["ok"]
        totals["failed"] += s["failed"]
    print(f"\n=== Segment summary: {totals['ok']} ok, {totals['failed']} failed ===")


def refresh_all(*, force: bool = False) -> None:
    tickers = [c[0] for c in COMPANIES if c[0] not in BENCHMARK_TICKERS]
    print(f"All companies: {len(tickers)} tickers")
    totals = {"ok": 0, "failed": 0, "skipped": 0}
    for t in tickers:
        s = refresh_ticker(t, force=force)
        if s.get("skipped"):
            totals["skipped"] += 1
        totals["ok"] += s["ok"]
        totals["failed"] += s["failed"]
    print(f"\n=== ALL: {totals['ok']} ok, {totals['failed']} failed, {totals['skipped']} non-filers skipped ===")


# ---------------- exhibit backfill ----------------


def backfill_exhibits(*, only_missing: bool = True) -> None:
    """Re-fetch earnings exhibits for already-downloaded 8-K/6-K filings.

    The exhibit finder was improved after the initial bulk download, so some
    filings (e.g. NVDA's `qNfyNNpr.htm` press release) have empty `exhibits` in
    their meta.json. This walks ok 8-K-earnings/6-K records and downloads the
    missing exhibits without touching the large primary docs."""
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, accession, filing_type, local_path FROM filings "
            "WHERE filing_type IN ('8-K-earnings','6-K') AND status='ok' "
            "ORDER BY ticker, filed_at DESC",
        ).fetchall()

    cik_cache: dict[str, int | None] = {}
    scanned = filled = added = 0
    for ticker, accession, ftype, local_path in rows:
        folder = (PROJECT_ROOT / local_path).parent
        meta_path = folder / "meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:  # noqa: BLE001
                meta = {}
        if only_missing and meta.get("exhibits"):
            continue
        if ticker not in cik_cache:
            cik_cache[ticker] = _resolve_cik(ticker)
        cik = cik_cache[ticker]
        if cik is None:
            continue
        scanned += 1

        f = Filing(
            ticker=ticker, cik=cik, form=("8-K" if ftype == "8-K-earnings" else "6-K"),
            accession=accession, filed_at=meta.get("filed_at", ""),
            period_end=meta.get("period_end", ""),
            primary_document=Path(local_path).name, items="2.02",
        )
        try:
            if ftype == "8-K-earnings":
                planned = [ExhibitPlan(kind, name, archive_url(cik, accession, name))
                           for name, kind in find_earnings_exhibits(f)]
            else:
                ex = _largest_content_exhibit(f)
                planned = [ex] if ex else []
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] {ticker} {accession}: archive list failed: {exc}")
            continue

        ex_meta: list[dict] = []
        for ex in planned:
            dest = folder / ex.filename
            try:
                if dest.exists() and dest.stat().st_size >= 2_000:
                    body = dest.read_bytes()
                else:
                    body = download(ex.url)
                    if len(body) < 2_000:
                        continue
                    _atomic_write(dest, body)
                    added += 1
                ex_meta.append({
                    "kind": ex.kind, "filename": ex.filename, "url": ex.url,
                    "sha256": _sha256(body), "size_bytes": len(body),
                })
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] {ticker} {ex.filename}: {exc}")
        if ex_meta:
            meta.setdefault("ticker", ticker)
            meta.setdefault("accession", accession)
            meta["exhibits"] = ex_meta
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(meta, indent=2))
            filled += 1
            print(f"  [ok]   {ticker:<6} {accession}  +{len(ex_meta)} exhibit(s): "
                  f"{', '.join(e['filename'] for e in ex_meta)}")
    print(f"\n=== backfill: {scanned} scanned, {filled} filings filled, {added} files downloaded ===")


# ---------------- verify / status ----------------


def verify_ticker(ticker: str) -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT filing_type, period_end, local_path, sha256, size_bytes "
            "FROM filings WHERE ticker=? AND status='ok' ORDER BY filing_type, period_end",
            (ticker.upper(),),
        ).fetchall()
    if not rows:
        print(f"No ok filings for {ticker}.")
        return
    print(f"Verifying {len(rows)} filings for {ticker}...")
    bad = 0
    for filing_type, period_end, local_path, expected_sha, expected_size in rows:
        path = PROJECT_ROOT / local_path
        if not path.exists():
            print(f"  MISSING  {filing_type} {period_end}  {local_path}")
            bad += 1
            continue
        body = path.read_bytes()
        size_ok = len(body) == expected_size
        sha_ok = _sha256(body) == expected_sha
        v_ok, v_msg = validate_content(filing_type, body, period_end or "")
        if size_ok and sha_ok and v_ok:
            print(f"  OK       {filing_type:<13} {period_end}  {len(body)//1024:>6} KB")
        else:
            print(f"  FAIL     {filing_type:<13} {period_end}  size_ok={size_ok} sha_ok={sha_ok} content={v_msg}")
            bad += 1
    print(f"  -> {len(rows) - bad} ok, {bad} bad")


def show_status() -> None:
    init_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, filing_type, period_end, status, validation_msg, size_bytes "
            "FROM filings ORDER BY status DESC, ticker, filing_type, period_end"
        ).fetchall()
    if not rows:
        print("No filings recorded.")
        return
    ok = sum(1 for r in rows if r[3] == "ok")
    print(f"{ok} ok, {len(rows) - ok} failed (out of {len(rows)} total)\n")
    print(f"{'TICKER':<8}{'TYPE':<14}{'PERIOD':<12}{'STATUS':<8}{'SIZE':>10}  MSG")
    for r in rows:
        size = f"{(r[5] or 0)//1024} KB" if r[5] else ""
        print(f"{r[0]:<8}{r[1]:<14}{(r[2] or ''):<12}{r[3]:<8}{size:>10}  {r[4] or ''}")


# ---------------- CLI ----------------


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            "  python -m backend.filings refresh <TICKER>\n"
            "  python -m backend.filings refresh \"<Segment name>\"\n"
            "  python -m backend.filings refresh all\n"
            "  python -m backend.filings refresh <TICKER> --force\n"
            "  python -m backend.filings verify <TICKER>\n"
            "  python -m backend.filings status"
        )
        return
    cmd = args[0]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    if cmd == "refresh":
        if len(args) < 2:
            raise SystemExit("refresh needs a target")
        target = args[1]
        if target == "all":
            refresh_all(force=force)
        elif target in SEGMENTS:
            refresh_segment(target, force=force)
        else:
            refresh_ticker(target, force=force)
    elif cmd == "verify":
        if len(args) < 2:
            raise SystemExit("verify needs a TICKER")
        verify_ticker(args[1])
    elif cmd == "backfill":
        backfill_exhibits(only_missing="--all" not in sys.argv)
    elif cmd == "status":
        show_status()
    else:
        raise SystemExit(f"Unknown command: {cmd!r}")


if __name__ == "__main__":
    main()
