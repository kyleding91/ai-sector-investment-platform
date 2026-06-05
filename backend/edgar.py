"""SEC EDGAR client for the AI-sector watchlist.

- Resolves ticker -> CIK via SEC's canonical company_tickers.json.
- Lists recent filings via the submissions JSON over a superset of forms so the
  data decides the filer type: US domestic filers report 10-K / 10-Q / 8-K,
  while foreign private issuers (TSM, ASML, UMC, ARM, NBIS) report 20-F / 6-K.
- Filters 8-K filings to earnings releases (Item 2.02 = Results of Operations).
- Downloads primary docs and exhibits with the SEC-required User-Agent header
  and a polite throttle (well under SEC's 10 req/s cap).

Samsung (005930) and SK hynix (000660) don't file with the SEC — they are not
in company_tickers.json — so they resolve to nothing and are skipped upstream.
"""
from __future__ import annotations

import json
import re as _re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

USER_AGENT = "AI Sector Investment Platform kyleding91@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
THROTTLE_SECONDS = 0.15  # ~6.6 req/s, comfortably under SEC's 10 req/s

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "edgar_cache"

# All forms we care about. Domestic vs foreign-private-issuer is inferred from
# whichever of these a company actually files (10-K => domestic, 20-F => FPI).
ALL_FORMS = ("10-K", "10-Q", "8-K", "20-F", "40-F", "6-K")

_session = requests.Session()
_session.headers.update(HEADERS)
_last_request_at = 0.0


MAX_RETRIES = 4  # ride over transient SEC connection drops / DNS blips


def _http_get(url: str, *, stream: bool = False, timeout: int = 30) -> requests.Response:
    """Polite GET — enforces throttle + user-agent, retries transient network
    errors with exponential backoff. Raises on non-2xx or exhausted retries."""
    global _last_request_at
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        delta = time.monotonic() - _last_request_at
        if delta < THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS - delta)
        try:
            resp = _session.get(url, stream=stream, timeout=timeout)
            _last_request_at = time.monotonic()
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout) as exc:
            _last_request_at = time.monotonic()
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise last_exc  # type: ignore[misc]


def get_ticker_cik_map(*, refresh: bool = False) -> dict[str, int]:
    """Return {TICKER: cik_int} from SEC's authoritative mapping. Cached on disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "company_tickers.json"
    if refresh or not cache.exists():
        resp = _http_get("https://www.sec.gov/files/company_tickers.json")
        cache.write_bytes(resp.content)
    raw = json.loads(cache.read_text())
    out: dict[str, int] = {}
    for row in raw.values():
        out[row["ticker"].upper()] = int(row["cik_str"])
    return out


def cik10(cik: int) -> str:
    """Zero-pad CIK to 10 digits (required by data.sec.gov)."""
    return f"{cik:010d}"


def accession_no_dashes(acc: str) -> str:
    return acc.replace("-", "")


def archive_url(cik: int, accession: str, filename: str) -> str:
    """Build a URL to a file inside an EDGAR filing archive."""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_no_dashes(accession)}/{filename}"
    )


@dataclass
class Filing:
    ticker: str
    cik: int
    form: str
    accession: str          # with dashes, e.g. '0000019617-26-000123'
    filed_at: str           # 'YYYY-MM-DD'
    period_end: str         # 'YYYY-MM-DD'
    primary_document: str   # filename inside the archive
    items: str              # comma-separated 8-K items, '' for others

    @property
    def primary_doc_url(self) -> str:
        return archive_url(self.cik, self.accession, self.primary_document)

    @property
    def index_url(self) -> str:
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
            f"{accession_no_dashes(self.accession)}/index.json"
        )


def _bucket_to_filings(
    ticker: str,
    cik: int,
    bucket: dict,
    forms_set: set[str],
    since: str | None,
) -> list[Filing]:
    out: list[Filing] = []
    n = len(bucket.get("form", []))
    for i in range(n):
        form = bucket["form"][i]
        if form.upper() not in forms_set:
            continue
        filed_at = bucket["filingDate"][i]
        if since and filed_at < since:
            continue
        out.append(Filing(
            ticker=ticker,
            cik=cik,
            form=form,
            accession=bucket["accessionNumber"][i],
            filed_at=filed_at,
            period_end=bucket["reportDate"][i] or filed_at,
            primary_document=bucket["primaryDocument"][i],
            items=(bucket["items"][i] or "") if "items" in bucket else "",
        ))
    return out


def list_filings(
    ticker: str,
    cik: int,
    *,
    forms: Iterable[str] = ALL_FORMS,
    since: str | None = None,
) -> list[Filing]:
    """Pull filings via the submissions endpoint.

    `filings.recent` holds up to ~1000 most-recent entries. Foreign issuers like
    TSM file frequent monthly-sales 6-Ks, so older 10-K/20-F can fall out of the
    recent bucket — we follow `filings.files[]` history pointers when `since`
    predates the recent window.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json"
    data = _http_get(url).json()
    forms_set = {f.upper() for f in forms}

    recent = data["filings"]["recent"]
    out = _bucket_to_filings(ticker, cik, recent, forms_set, since)

    older_pointers = data["filings"].get("files", [])
    if since and recent.get("filingDate"):
        oldest_in_recent = min(recent["filingDate"]) if recent["filingDate"] else None
        if oldest_in_recent and since < oldest_in_recent:
            for ptr in older_pointers:
                if ptr.get("filingTo", "") < since:
                    continue
                file_url = f"https://data.sec.gov/submissions/{ptr['name']}"
                older = _http_get(file_url).json()
                out.extend(_bucket_to_filings(ticker, cik, older, forms_set, since))
    return out


def filter_8k_earnings(filings: Iterable[Filing]) -> list[Filing]:
    """Keep only 8-Ks whose `items` string contains '2.02' (Results of Operations)."""
    return [f for f in filings if f.form == "8-K" and "2.02" in f.items]


def list_archive_files(filing: Filing) -> list[dict]:
    """List every file in the filing's archive (so we can find Exhibit 99.1, etc.)."""
    data = _http_get(filing.index_url).json()
    return data.get("directory", {}).get("item", [])


# Match 'ex'/'exh'/'exhibit' followed by 99 and an optional 1/2/3 digit.
_EXHIBIT_RE = _re.compile(r"ex(?:hibit|h)?[\W_dx]*99[\W_dx]*([1-3]?)")


def find_earnings_exhibits(filing: Filing) -> list[tuple[str, str]]:
    """For an earnings 8-K, find press-release / supplement / presentation exhibits.

    Returns (filename, kind) where kind is press_release | supplement | presentation.
    """
    items = list_archive_files(filing)
    primary_lower = filing.primary_document.lower()
    out: list[tuple[str, str]] = []
    seen_kinds: set[str] = set()

    candidates = [
        it.get("name", "") for it in items
        if it.get("name", "").lower().endswith((".htm", ".html", ".pdf"))
        and it.get("name", "").lower() != primary_lower
    ]

    KEYWORD_KIND = [
        ("shareholderletter", "press_release"),
        ("pressrelease",      "press_release"),
        ("earningsrelease",   "press_release"),
        ("pressrel",          "press_release"),
        ("earningsr",         "press_release"),
        ("financialdata",     "supplement"),
        ("financialdetails",  "supplement"),
        ("financialhighlights", "supplement"),
        ("cfocommentary",     "supplement"),   # NVDA: q1fy27cfocommentary.htm
        ("commentary",        "supplement"),
        ("supplement",        "supplement"),
        ("supp",              "supplement"),
        ("presentation",      "presentation"),
        ("present",           "presentation"),
        ("deck",              "presentation"),
        ("slides",            "presentation"),
        ("release",           "press_release"),
        ("earnings",          "press_release"),
    ]

    def _kind_from_keywords(low: str) -> str | None:
        compact = low.replace("-", "").replace("_", "").replace(".", "")
        for kw, kind in KEYWORD_KIND:
            if kw in compact:
                return kind
        # Compact suffix conventions after a quarter/FY number:
        #   '...26er.htm' (earnings release), '...q1fy27pr.htm' (press release).
        if _re.search(r"\d+er(?:[^a-z]|$)", low):
            return "press_release"
        if _re.search(r"\d+pr(?:[^a-z]|$)", low):
            return "press_release"
        return None

    # Pass 1: explicit ex99X filenames — most reliable.
    for name in candidates:
        low = name.lower()
        m = _EXHIBIT_RE.search(low)
        if not m:
            continue
        idx = m.group(1)
        if idx:
            kind = {"1": "press_release", "2": "supplement", "3": "presentation"}[idx]
        else:
            kind = _kind_from_keywords(low) or "press_release"
        if kind in seen_kinds:
            continue
        out.append((name, kind))
        seen_kinds.add(kind)

    # Pass 2: keyword-only fallback for filers that don't use ex99X naming.
    for name in candidates:
        low = name.lower()
        if any(name == n for n, _ in out):
            continue
        kind = _kind_from_keywords(low)
        if kind and kind not in seen_kinds:
            out.append((name, kind))
            seen_kinds.add(kind)

    return out


def download(url: str) -> bytes:
    return _http_get(url, stream=True).content
