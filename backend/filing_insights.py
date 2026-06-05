"""Extractive "in their own words" insights pulled straight from the downloaded
SEC filings — no LLM required.

For each company we read the latest annual report on disk (10-K for domestic
filers, 20-F for foreign private issuers) and pull:
  * self_description  — the company's own "We are / We design ..." sentence(s)
  * business_overview — the opening of the Business section (Item 1 / Item 4)
  * segments_note     — how the filing describes its reportable segments
  * strategy_points   — bulleted strategy / "Our strategy" lines when present
And from the latest earnings release (8-K Exhibit 99.1, or a 6-K content
exhibit) we pull management quotes — literally how leadership talks about the
business.

Everything is verbatim company language, so it needs no "AI-generated" caveat;
it's sourced text with a link back to the filing on SEC EDGAR.

CLI:
    python -m backend.filing_insights refresh NVDA
    python -m backend.filing_insights refresh all
    python -m backend.filing_insights show NVDA
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from backend.companies import BENCHMARK_TICKERS, COMPANIES
from backend.db import connect, init_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METHOD = "extractive (SEC filings)"

_NAME_BY_TICKER = {c[0]: c[2] for c in COMPANIES}

# Tags whose *contents* we never want as text.
_SKIP_TAGS = {"script", "style", "ix:hidden", "ix:header", "head", "title"}
# Block-level tags that imply a line break in the rendered text.
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "ul", "ol", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr",
}


class _TextExtractor(HTMLParser):
    """Turn filing HTML into readable plain text, preserving block breaks and
    dropping inline-XBRL hidden fact blobs that would otherwise pollute the top."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Normalise NBSP and friends, collapse intra-line whitespace, tidy blanks.
        raw = raw.replace(" ", " ").replace("​", "")
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{2,}", "\n\n", raw)
        return raw.strip()


def html_to_text(data: bytes) -> str:
    p = _TextExtractor()
    try:
        p.feed(data.decode("utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001 — malformed markup shouldn't kill the run
        pass
    return p.text()


# ---------------- section location ----------------


def _best_span(text: str, start_pat: str, end_pat: str) -> str | None:
    """Pick the (start,end) pair with the largest gap — this skips the short
    table-of-contents line and lands on the real section body."""
    starts = [m.end() for m in re.finditer(start_pat, text, re.I)]
    ends = [m.start() for m in re.finditer(end_pat, text, re.I)]
    if not starts or not ends:
        return None
    best = None
    best_gap = 2_000  # require a real body, not a TOC entry
    for s in starts:
        later = [e for e in ends if e > s]
        if not later:
            continue
        e = min(later)
        if e - s > best_gap:
            best, best_gap = (s, e), e - s
    if best is None:
        return None
    return text[best[0]:best[1]].strip()


# Definitional openers used to locate the business narrative when the formal
# Item headers can't be matched (cross-referenced 10-Ks, integrated 20-F reports).
# Investor-relations / SEC-availability boilerplate that often sits near the top
# of Item 1 and must not be mistaken for the business narrative.
_ANCHOR_SKIP = ("available", "website", "investor relations", "interested persons",
                "incorporated by reference", "filed with", "sec.gov", "edgar")
# 'We are a/the ...' openings that are legal/structural boilerplate, not business.
_DEF_FALSE_POS = ("public accounting firm", "foreign private issuer",
                  "company limited by shares", "variable interest entity",
                  "primary beneficiary", "smaller reporting company",
                  "party. these", "subject to", "required to",
                  "honorary member", "foundation board", "executive committee",
                  "world economic forum", "board of directors",
                  "cayman islands", "exempted company", "incorporated under",
                  "incorporated in", "holding company", "judicial precedent")


def _first_acceptable(text: str, pat: str, *, floor: int = 2500) -> int | None:
    """Earliest match of `pat` past `floor` whose local context isn't boilerplate.
    Case-sensitive on purpose: a capitalised 'We'/'Our' marks a sentence start,
    so we don't latch onto a lowercase 'we are a party ...' mid-sentence."""
    for m in re.finditer(pat, text):
        if m.start() <= floor:
            continue
        ctx = text[max(0, m.start() - 40):m.start() + 200].lower()
        if any(b in ctx for b in _ANCHOR_SKIP) or any(b in ctx for b in _DEF_FALSE_POS):
            continue
        if "forward-looking" in ctx:
            continue
        return m.start()
    return None


def _overview_heading_anchor(text: str, name: str, *, floor: int = 2500) -> int | None:
    """An 'Overview' heading immediately followed by a defining sentence — the
    most reliable business intro across filers (CDNS, CSCO, ARM, UMC, MU...)."""
    nm = re.escape(name)
    follow = re.compile(
        rf"^\s*(?:{nm}\b|We\s+(?:are|design|develop|provide|offer|deliver|"
        rf"architect|operate|combine|make|build)\b)", re.I)
    for m in re.finditer(r"\n[ \t]*overview[ \t]*\n+", text, re.I):
        if m.start() <= floor:
            continue
        tail = text[m.end():m.end() + 160]
        if follow.match(tail):
            return m.end()
    return None


def _definitional_anchor(text: str, name: str) -> int | None:
    """Offset of the strongest 'who we are' sentence, skipping the cover page /
    table of contents and IR / forward-looking / legal boilerplate. Prefers an
    'Overview' heading + definition; then '<Name> is ...'; then 'We are a/the ...';
    then 'We <verb> ...'."""
    nm = re.escape(name)
    head = _overview_heading_anchor(text, name)
    if head is not None:
        return head
    hits = [
        _first_acceptable(text, pat)
        for pat in (
            nm + r"\s+is\s+(?:a|an|the|now|one\s+of)\b",
            r"\bWe\s+are\s+(?:a|an|the|now|one\s+of|primarily)\b",
            r"\bWe\s+(?:design|develop|provide|offer|deliver|build|operate)\b",
            r"\bOur\s+(?:mission|purpose)\s+(?:is|are)\b",
        )
    ]
    hits = [h for h in hits if h is not None]
    return min(hits) if hits else None


def _anchored_section(text: str, name: str) -> str | None:
    start = _definitional_anchor(text, name)
    if start is None:
        return None
    # Back up to the start of the sentence so the overview reads cleanly.
    window = text[max(0, start - 300):start]
    pre = max(window.rfind(". "), window.rfind("\n"))
    if pre != -1:
        start = max(0, start - 300) + pre + 1
    return text[start:start + 8000].strip()


def extract_business_section(text: str, form: str, name: str | None = None) -> str | None:
    if form == "20-F":
        # Item 4 "Information on the Company" -> Item 5 "Operating and Financial Review".
        sec = _best_span(
            text,
            r"item\s*4[\.\s:]+information\s+on\s+the\s+company",
            r"item\s*5[\.\s:]+operating",
        )
        if sec:
            return sec
        # Fallback: Item 4.B "Business Overview" -> "Organizational Structure".
        sec = _best_span(text, r"business\s+overview", r"organi[sz]ational\s+structure")
    else:
        # 10-K: Item 1 "Business" -> Item 1A "Risk Factors".
        sec = _best_span(text, r"item\s*1[\.\s:]+business", r"item\s*1a[\.\s:]+risk\s+factors")
        if not sec:
            sec = _best_span(text, r"item\s*1[\.\s:]+business", r"item\s*2[\.\s:]+properties")
    if sec:
        return sec
    # Last resort: anchor on the company's own definitional sentence. Catches
    # cross-referenced 10-Ks (INTC) and integrated annual-report 20-Fs (ASML).
    if name:
        return _anchored_section(text, name)
    return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# Corporate suffixes to strip when deriving the short name companies use in prose.
_SUFFIX_RE = re.compile(
    r"\b(Corporation|Incorporated|Inc|Company|Co|plc|N\.?V|Ltd|Limited|Holdings?|"
    r"Group|Technologies|Technology|Systems|Networks|Semiconductor[s]?|"
    r"Manufacturing|Devices|Platforms|Materials|Research|Design|Labs?)\b\.?",
    re.I,
)


def short_name(legal_name: str) -> str:
    """'NVIDIA Corporation' -> 'NVIDIA'; 'Micron Technology, Inc.' -> 'Micron'."""
    n = legal_name.split(",")[0]
    n = _SUFFIX_RE.sub("", n)
    n = re.sub(r"\s+", " ", n).strip(" .,")
    return n or legal_name.split()[0]


# Verb lemmas that signal a self-definition rather than an incidental mention —
# matched with word boundaries so 'designs'/'develops'/'architects' count too.
_DEFINE_RE = re.compile(
    r"\b(?:is|are)\s+(?:a|an|the|now|one\b)|\b(?:mission|purpose)\s+is\b|"
    r"\b(?:design|develop|provide|operate|deliver|make|build|enable|offer|"
    r"architect|license|supply|combine|pioneer|create|sell|power)(?:s|ed)?\b",
    re.I,
)

# Phrases that mark a sentence as boilerplate (forward-looking disclaimers, risk
# factors, buybacks, incidental competitive mentions) rather than a self-definition.
_DESC_BADWORDS = (
    "forward-looking", "forward looking", "urge you", "no assurance",
    "cannot provide", "cannot assure", "any assurances", "assurance that",
    "litigation", "repurchas", "consider these factors", "evaluating the",
    "competition from", "we also sometimes", "risk factors", "see item",
    "this report", "this annual report", "this form", "should be read",
    "estimates", "may differ materially", "uncertainties",
    "annual report on form", "quarterly reports", "current reports",
    "available free of charge", "filed with the", "make available",
    "investor relations", "interested persons", "our website", "sec.gov",
)


def extract_self_description(section: str, name: str) -> str | None:
    """First 1-2 sentences where the company defines itself — handles both
    'We are ...' and '<Company> is a ...' openings."""
    head = section[:9000]
    sentences = re.split(r"(?<=[.])\s+(?=[A-Z“\"])", head)
    lead_re = re.compile(
        r"^(?:" + re.escape(name) + r"|We|The\s+Company|Our\s+(?:mission|purpose))\b", re.I)
    candidates = [(_clean(s)) for s in sentences if lead_re.match(_clean(s))]
    candidates = [
        s for s in candidates
        if 40 <= len(s) <= 600
        and not any(b in s.lower() for b in _DESC_BADWORDS)
    ]
    if not candidates:
        return None
    # Require a clearly definitional sentence — a misleading fragment is worse
    # than nothing (the business_overview still carries the description).
    defining = [s for s in candidates if _DEFINE_RE.search(s)]
    if not defining:
        return None
    chosen = defining[0]
    # If the chosen sentence is short, append the next candidate for context.
    if len(chosen) < 140 and len(candidates) > 1:
        nxt = candidates[1] if candidates[0] == chosen else candidates[0]
        if nxt != chosen:
            chosen = _clean(chosen + " " + nxt)
    return chosen[:600]


def _trim_preamble(section: str, name: str) -> str:
    """Slice past a leading cautionary / forward-looking / risk / TOC preamble to
    the first genuine 'who we are / what we do' anchor, so self-description and
    overview don't quote disclaimers. Returns the section unchanged if no anchor."""
    pats = [
        r"\b(?:Our\s+Business|Company\s+Overview|Business\s+Overview)\b",
        re.escape(name) + r"\s+is\s+(?:a|an|the|now|one\s+of)\b",
        r"\bWe\s+are\s+(?:a|an|the|now|one\s+of|primarily)\b",
        r"\bWe\s+(?:design|develop|provide|offer|deliver|build|operate)\b",
    ]
    best: int | None = None
    for pat in pats:
        for m in re.finditer(pat, section):
            ctx = section[max(0, m.start() - 40):m.start() + 180].lower()
            if any(b in ctx for b in _ANCHOR_SKIP) or "forward-looking" in ctx:
                continue
            if best is None or m.start() < best:
                best = m.start()
            break
    if best is None or best > 12000:
        return section
    return section[best:]


def extract_overview(section: str, self_desc: str | None) -> str | None:
    """A clean opening excerpt of the Business section, trimmed to a sentence end.
    Continues *after* the self-description sentence(s) so the UI's bold lead quote
    isn't repeated verbatim in the paragraph below it."""
    body = _clean(section)
    # Drop a leading 'Overview'/'General'/'Our Company' subheading word.
    body = re.sub(r"^(overview|general|our\s+company|company\s+overview)\b[:\s]*", "",
                  body.strip(), flags=re.I)
    if self_desc:
        sd = _clean(self_desc)
        idx = body.find(sd[:60])
        if idx == -1:
            idx = body.find(sd[:30])
        if idx != -1:
            # Skip past the sentence(s) already shown as the self-description.
            n_sent = max(1, len(re.findall(r"\.(?:\s|$)", sd)))
            pos = idx
            for _ in range(n_sent):
                nxt = body.find(". ", pos)
                if nxt == -1:
                    pos = idx
                    break
                pos = nxt + 2
            body = body[pos:].lstrip(" .—-") if pos > idx else body[idx:]
    if not body:
        return None
    excerpt = body[:1100]
    # Trim to the last sentence boundary so we don't cut mid-thought.
    cut = excerpt.rfind(". ")
    if cut > 400:
        excerpt = excerpt[:cut + 1]
    return excerpt


_SEG_BADWORDS = (
    "$", "operating income", "gross margin", "year over year", "year-over-year",
    "increase", "decrease", "%", "recently adopted", "accounting standard",
    "accounting pronouncement", "asu ", "asu2", "goodwill", "impairment", "fair value",
    "notes to consolidated", "estimation", "qualify for aggregation", "we do not allocate",
    "not evaluated", "do not allocate", "reporting units", "amortization", "see note",
    "refer to note", "stock-based", "chief operating decision maker", "codm",
    "reviews financial", "management approach", "method that management", "tax authorities",
    "the reserve", "sustainability report", "progress toward", "may face", "competition",
    "competitors", "discrete financial information", "allocates resources to",
    "directly attributable", "charged to", "directly associated", "costs or expenses",
    "operating results by segment", "table of contents",
)
# A sentence must look STRUCTURAL (defines/counts/names segments) to qualify — this
# screens out incidental 'market segment' / methodology mentions.
_SEG_STRUCT = re.compile(
    r"(?:reportable|operating)\s+segments?"
    r"|segments?\s*:"
    r"|\b(?:one|two|three|four|five|six|seven|single|following)\b[^.]{0,45}\bsegments?\b"
    r"|\bsegments?\b[^.]{0,35}\b(?:are|consist|include|comprise|named)\b",
    re.I)
_SEG_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|single|following|\d+)\b", re.I)
# Names the segments: a list after a colon/comma, or 'segments are/consist/include ...'.
_SEG_NAMES = re.compile(
    r"segments?\b[^.]*[:,]\s*[•“\"A-Z]|segments?\s+(?:are|consist|include|comprise|named|reflect)\b"
    r"|:\s*[•“\"A-Z]", re.I)
# Accounting-note / results tells that slip past the badword gate but should still
# sink a candidate well below a clean structural sentence.
_SEG_PENALTY = (
    "note ", "calculation", "net income", "other income", "accounting polic",
    "further described", "in millions", "increased by", "decreased by",
    "summary of significant", "reviews financial",
)
# A sentence that defines the company's segment STRUCTURE (not a results/notes mention).
_SEG_DEF = re.compile(
    r"\b(?:we|the\s+company|our)\b[^.]*?\b(?:report|have|operate|organized|organize|manage|"
    r"determined|tracks?)\b[^.]*?\b(?:reportable\s+segments?|operating\s+segments?|"
    r"business\s+units?|segments?)\b", re.I)
# Leading section-title echoes to strip from the chosen sentence.
_SEG_ECHO = re.compile(
    r"^(?:part\s+[ivxlc]+\s+)?(?:item\s+\d+[a-z]?[.\-–—\s]*)?"
    r"(?:segment\s+reporting(?:\s+segment\s+information)?|operating\s+segment\s+information|"
    r"reportable\s+segments?|segment,?\s+geographic[^.]*?information|financial\s+information\s+"
    r"about\s+segments?[^.]*?|industry,?\s+segment\s+and\s+geographic\s+information|"
    r"reporting\s+segments?|operating\s+segments?|our\s+businesses|"
    r"segment\s+and\s+geographic\s+(?:information|areas?))\b[\s:.\-–—()0-9]*", re.I)
# A trailing subordinate clause that leads into CODM / accounting-note language —
# dropped so a clean structural statement survives the badword gate.
_SEG_TAIL = re.compile(
    r"[,;]?\s+(?:which|aligning|reflecting|whose|and\s+reflects?|as\s+the\s+\w+|"
    r"that\s+reflects?)\b.*$", re.I)


def _seg_pretrim(s: str) -> str:
    """Drop a trailing 'which reflects ... chief operating decision maker ...' clause
    so the structural lead survives the accounting-note badword filter."""
    low = s.lower()
    if any(k in low for k in ("decision maker", "codm", "reviews financial",
                              "executes operating", "further described")):
        s2 = _SEG_TAIL.sub("", s).rstrip(" ,;")
        if len(s2) >= 24:
            if not s2.endswith("."):
                s2 += "."
            return s2
    return s


def _seg_trim(s: str) -> str:
    """Trim a long 'we have N segments: a; b; c' sentence to a clean, bounded note."""
    orig = s.strip()
    s = _SEG_ECHO.sub("", orig).strip()
    if "segment" not in s.lower():
        s = orig  # echo strip removed the only 'segment' mention — keep it intact
    # If a title-case heading fragment still precedes the real clause, jump to the
    # subject — but only when the prefix looks like a heading and the jump keeps the
    # word 'segment' in the note (never strip the segment context away).
    m = re.search(r"\b(?:We|Our|The\s+Company|The\s+Group)\b", s)
    if m and 0 < m.start() <= 60:
        prefix = s[:m.start()].strip()
        cand = s[m.start():]
        if (re.fullmatch(r"[A-Za-z][A-Za-z&/’'\-]*(?:\s+[A-Za-z&/’'\-]+){0,5}", prefix)
                and "segment" in cand.lower()):
            s = cand
    # Bulleted lists ("...segments: • Compute • Graphics") → readable inline list.
    s = re.sub(r"\s*[•·]\s*", "; ", s).strip("; ").strip()
    s = re.sub(r":\s*;\s*", ": ", s)
    s = re.sub(r"\s{2,}", " ", s)
    if len(s) <= 320:
        return s
    cut = s.rfind("; ", 0, 320)
    if cut < 120:
        cut = s.rfind(", ", 0, 320)
    return (s[:cut] if cut > 120 else s[:317].rstrip()) + "…"


def extract_segments_note(text: str) -> str | None:
    """How the filing defines its reportable/operating segments — e.g.
    'We have organized our operations into three segments: ...' — preferring the
    sentence that names or counts the segments over an accounting-note mention."""
    # Split on sentence end — tolerate a closing quote after the period and a
    # page-number/marker token between sentences ("segment. 78 Table of Contents ...")
    # so a clean naming sentence isn't merged with adjacent notes/page chrome.
    # NOTE: split on '.' only, not ';' — segment lists use ';' as item separators
    # ("segments: Americas; EMEA; APJC"), so splitting on ';' would truncate them.
    sentences = re.split(r'(?<=[.])["”’]?\s+(?:\d{1,4}\s*[-–—.]?\s*)?(?=[A-Z“"(])', text)
    cands = []
    for s in sentences:
        s = _seg_pretrim(_clean(s))
        low = s.lower()
        if "segment" not in low:
            continue
        if not (28 <= len(s) <= 700 and s[:1].isupper()):
            continue
        if any(b in low for b in _SEG_BADWORDS):
            continue
        if not _SEG_STRUCT.search(s):
            continue
        cands.append(s)
    if not cands:
        return None

    def score(s: str) -> int:
        low = s.lower()
        sc = 0
        # Strongest: a subject-led sentence that defines the segment structure.
        if _SEG_DEF.search(s):
            sc += 3
        if re.search(r"^(?:we|our|the\s+company|the\s+group)\b", low):
            sc += 2
        # A count/'single' that actually qualifies 'segment' (not a stray 'Note 3').
        if re.search(r"\b(?:one|two|three|four|five|six|seven|single|\d+)\b"
                     r"[^.]{0,40}\b(?:reportable\s+|operating\s+)?segments?\b", low):
            sc += 2
        # Explicitly names the segments via a colon list.
        if re.search(r"(?:reportable|operating|geographic)\s+segments?\s*:\s*[A-Z“\"]", s):
            sc += 3
        if re.search(r"\b(reportable|operating)\s+segments?\b", low):
            sc += 1
        for p in _SEG_PENALTY:
            if p in low:
                sc -= 4
        return sc

    ranked = sorted(range(len(cands)), key=lambda i: (-score(cands[i]), i))
    best = cands[ranked[0]]
    if score(best) <= 0:
        return None
    return _seg_trim(best)


# A 'strategy'/'strengths' heading line in the body of the filing.
_STRAT_HEAD = re.compile(
    r"\n[ \t]*(?:our\s+|the\s+company['’]s\s+)?"
    r"(?:business\s+|growth\s+|company\s+|corporate\s+|key\s+)?"
    r"(?:strateg(?:y|ies)|strategic\s+(?:priorit|focus|objectives|initiatives)|"
    r"competitive\s+strengths?|key\s+strengths?)[ \t]*\n",
    re.I,
)
# Sentences that shouldn't be presented as 'strategy' even if they match a lead.
_STRAT_BAD = (
    "forward-looking", "risk factor", "may differ", "no assurance",
    "table of contents", "see item", "item 1a", "could harm", "adversely affect",
    "we cannot assure", "deposit agreement", "the adss", "trade policy", "tariff",
)
_STRAT_SECTION_TITLES = {
    "management's discussion and analysis", "overview", "general", "business",
    "properties", "our business", "competition", "employees", "human capital",
    "item 1.", "item 1",
}
# Strong lead-ins that mark a strategy/mission/priority statement.
_STRAT_LEAD = re.compile(
    r"^(?:Our\s+(?:strateg|mission|vision|goal|objective|purpose|priorit|approach|focus|"
    r"competitive\s+strength)|We\s+(?:intend|plan|aim|seek|strive|are\s+focused|"
    r"are\s+committed|are\s+pursuing)\s)", re.I)
# Weaker fallbacks, used only to top up to the limit.
_STRAT_LEAD2 = re.compile(
    r"^(?:We\s+(?:believe|continue|will\s+continue|expect\s+to|are\s+investing)|"
    r"Key\s+(?:to|elements)|The\s+(?:first|key)\s+element)", re.I)
# Words that push a candidate up (real business strategy) or down (HR / capital-return).
_STRAT_UP = ("strateg", "mission", "vision", "growth", "technolog", "innovat", "product",
             "customer", "market", "leadership", "platform", "invest in r",
             "research and development", "compute", "ai ", "portfolio", "scale",
             "manufactur", "design", "differentiat", "expand")
_STRAT_DOWN = ("employee", "workplace", "talent", "safety", "health", "ehs", "dividend",
               "human capital", "people practices", "diversity", "work environment",
               "retain earnings", "hiring", "compensation", "well-being", "wellbeing")
_STRAT_ECHO = re.compile(
    r"^(?:our\s+strateg(?:y|ies)|our\s+business|business\s+strateg(?:y|ies)|"
    r"our\s+competitive\s+strengths?|key\s+strengths?)\b[\s\d:.\-–—]*", re.I)


def _strat_take_lead(s: str) -> str:
    s = _STRAT_ECHO.sub("", s).strip()           # drop a leading heading echo
    first = re.split(r"(?<=[.])\s+(?=[A-Z])", s)[0]
    return first if 28 <= len(first) <= 260 else s[:260]


def _strat_ok(s: str, *, need_period: bool) -> bool:
    if not (28 <= len(s) <= 260):
        return False
    if not (s[:1].isalpha() and s[:1].isupper()):
        return False
    if need_period and not s.rstrip().endswith("."):
        return False
    if s.lower().rstrip(".") in _STRAT_SECTION_TITLES:
        return False
    if s.endswith(("include:", "following:", "are:", "are our:")):
        return False
    if any(b in s.lower() for b in _STRAT_BAD):
        return False
    # Must read like a real sentence (two run-on lowercase words), not a TOC fragment.
    return bool(re.search(r"[a-z]{3,}\s+\S*[a-z]{3,}", s))


def _strat_score(s: str) -> int:
    low = s.lower()
    return 2 * sum(w in low for w in _STRAT_UP) - 4 * sum(w in low for w in _STRAT_DOWN)


def _strat_heading_pool(text: str) -> list[str]:
    """Candidates under the first real 'strategy'/'strengths' heading (bullets or prose)."""
    for hm in _STRAT_HEAD.finditer(text):
        if hm.start() < 2500:
            continue
        ctx = text[max(0, hm.start() - 80):hm.start()].lower()
        if any(b in ctx for b in ("forward-looking", "risk factor", "item 1a")):
            continue
        chunk = text[hm.end(): hm.end() + 2600]
        bullets = chunk.count("•") >= 2
        pieces = ([_clean(p.split("\n\n")[0]) for p in chunk.split("•")[1:]] if bullets
                  else [_clean(p) for p in re.split(r"(?<=\.)\s+(?=[A-Z“\"])|\n\n", chunk)])
        pool: list[str] = []
        for s in pieces:
            s = _strat_take_lead(s.rstrip(";").strip())
            if _strat_ok(s, need_period=not bullets):
                pool.append(s)
            if len(pool) >= 6:
                break
        if len(pool) >= 3:
            return pool
    return []


def _strat_lead_pool(body: str, regex: re.Pattern) -> list[str]:
    pool: list[str] = []
    for s in re.split(r"(?<=[.])\s+(?=[A-Z“\"])", body):
        s = _clean(s)
        if regex.match(s):
            ls = _strat_take_lead(s)
            if _strat_ok(ls, need_period=False):
                pool.append(ls)
        if len(pool) >= 8:
            break
    return pool


def extract_strategy_points(text: str, section: str, limit: int = 4) -> list[str]:
    """How the company frames its own strategy/priorities, in its own words. Pools
    candidates from (1) a strategy heading, then (2) strong 'Our strategy/We intend'
    leads, then (3) weaker leads, ranks them so business-strategy lines beat
    HR/capital-return boilerplate, and returns the top `limit`."""
    pool: list[str] = []
    for cand in (
        _strat_heading_pool(text),
        _strat_lead_pool(section, _STRAT_LEAD),
        _strat_lead_pool(text, _STRAT_LEAD),
        _strat_lead_pool(section, _STRAT_LEAD2),
        _strat_lead_pool(text, _STRAT_LEAD2),
    ):
        for s in cand:
            if s not in pool:
                pool.append(s)
    ranked = sorted(range(len(pool)), key=lambda i: (-_strat_score(pool[i]), i))
    return [pool[i] for i in ranked[:limit]]


# ---------------- key risks (Item 1A / Item 3.D) ----------------

# A "Risk Factors Summary" / "Overview of risk factors" heading on its own line —
# modern 10-Ks (NVDA, AMD, MU…) and integrated 20-Fs (ASML, ARM) front-load a
# concise bulleted/tabular list of every risk headline, which is the cleanest,
# most representative source of "key risks in their own words".
_RISK_SUMMARY_HEAD = re.compile(
    r"\n[ \t]*(?:summary\s+of\s+risk\s+factors|risk\s+factors?\s+summary|"
    r"overview\s+of\s+risk\s+factors)[ \t]*\n", re.I)
# Category sub-headers ("Risks Related to Our Industry…") that group the list but
# aren't themselves risks.
_RISK_CAT = re.compile(r"^risks?\s+(?:related|relating|associated|arising)\b", re.I)
# A trailing category header that bleeds into a bullet when the next line lacks a
# bullet glyph ("Competition could… . Risks Related to Demand, Supply…").
_RISK_CAT_TAIL = re.compile(
    r"\s+Risks?\s+(?:Related|Relating|Associated|Arising)\s+to\b.*$", re.I)
_BULLET = "•"
# Page chrome / running headers that appear between table rows in 20-F layouts.
_RISK_CHROME = (
    "table of contents", "annual report", "strategic report", "corporate governance",
    " sustainability", "at a glance", "q&a with", "our business",
    "financial performance", "risk and security", "view on sec",
)
# Single-word "risk type" column labels in tabular overviews (ASML).
_RISK_TYPE_WORDS = {
    "strategic", "operations", "operation", "compliance", "other", "general",
    "legal", "financial", "market", "risk", "type", "risk type", "risk factor",
    "finance and reporting", "sustainability", "financials",
}
# Signals a sentence actually describes a risk (used by the section fallback).
_RISK_SIG = re.compile(
    r"\b(risk|could|may|might|harm|advers|affect|fail|unable|inabilit|subject\s+to|"
    r"depend|declin|disrupt|uncertain|volatil|downturn|unfavorab|breach|defect|"
    r"negativ|material|competit|shortage|fluctuat|impair|litigat|loss|cyber|delay|"
    r"interrupt|tariff|sanction|shortfall)", re.I)
# Boilerplate that opens a Risk Factors section but isn't a risk headline.
_RISK_BAD = (
    "forward-looking", "table of contents", "see item", "should read",
    "incorporated by reference", "exhaustive", "in conjunction with",
    "the following risk", "additional risk", "these risk", "each of these",
    "the risk factors", "set forth below", "described below",
)


def _risk_cand_ok(s: str) -> bool:
    """A summary-list candidate is a real, self-contained risk headline."""
    low = s.lower().strip()
    if not (24 <= len(s) <= 320):
        return False
    if not s[:1].isupper():
        return False
    if low in _RISK_TYPE_WORDS:
        return False
    if _RISK_CAT.match(s):
        return False
    if any(c in low for c in _RISK_CHROME):
        return False
    if low.startswith(("the following risk", "additional risk", "these risk",
                       "each of these")):
        return False
    # Must read like a sentence (two run-on lowercase words), not a heading scrap.
    return bool(re.search(r"[a-z]{3,}\s+\S*[a-z]{3,}", s))


def _risk_summary_factors(text: str, limit: int) -> list[str]:
    """Risk headlines from a 'Risk Factors Summary' / 'Overview of risk factors'
    block — bulleted (10-K) or one-per-row (20-F table)."""
    m = _RISK_SUMMARY_HEAD.search(text)
    if not m:
        return []
    region = text[m.end():m.end() + 9000]
    bullets = region.count(_BULLET) >= 3
    pieces = region.split(_BULLET) if bullets else region.split("\n")
    out: list[str] = []
    for p in pieces:
        s = _RISK_CAT_TAIL.sub("", _clean(p)).strip()
        if _risk_cand_ok(s) and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _risk_section_span(text: str, form: str) -> str | None:
    """The body of the Risk Factors section: Item 1A→1B/2 (10-K) or
    Item 3.D→Item 4 (20-F)."""
    if form == "20-F":
        return (_best_span(text, r"item\s*3[\.\s]*d[\.\s:]*\s*risk", r"item\s*4[\.\s:]")
                or _best_span(text, r"item\s*3[\.\s:]+key\s+information", r"item\s*4[\.\s:]"))
    return (_best_span(text, r"item\s*1a[\.\s:]+risk\s+factors", r"item\s*1b[\.\s:]")
            or _best_span(text, r"item\s*1a[\.\s:]+risk\s+factors", r"item\s*2[\.\s:]+propert"))


def _risk_fallback_factors(text: str, form: str, limit: int) -> list[str]:
    """For filers without a summary block: harvest the bold risk sub-headings.
    In the flattened text each heading is a short, sentence-like paragraph
    immediately followed by a much longer explanatory paragraph."""
    section = _risk_section_span(text, form)
    if not section:
        return []
    paras = [_clean(p) for p in re.split(r"\n\s*\n", section)]
    out: list[str] = []
    for i, p in enumerate(paras):
        if not (24 <= len(p) <= 300):
            continue
        if not p[:1].isupper() or not p.rstrip().endswith("."):
            continue
        if _RISK_CAT.match(p):
            continue
        low = p.lower()
        if any(b in low for b in _RISK_BAD):
            continue
        if not _RISK_SIG.search(p):
            continue
        nxt = paras[i + 1] if i + 1 < len(paras) else ""
        if len(nxt) < 200:  # a real heading is followed by an explanation paragraph
            continue
        first = re.split(r"(?<=[.])\s+(?=[A-Z])", p)[0]
        if 24 <= len(first) <= 300 and first not in out:
            out.append(first)
        if len(out) >= limit:
            break
    return out


def extract_risk_factors(text: str, form: str, limit: int = 5) -> list[str]:
    """3-5 verbatim "key risks in their own words" from the latest annual report.
    Prefers the company's own risk-summary list; falls back to the bold risk
    sub-headings in the Item 1A / Item 3.D body."""
    factors = _risk_summary_factors(text, limit)
    if len(factors) >= 3:
        return factors
    return _risk_fallback_factors(text, form, limit)


# ---------------- management quotes ----------------

# Attribution verbs companies use to introduce a leadership quote in an earnings release.
_SAY = r"(?:said|stated|commented|noted|added|continued|remarked|concluded|observed|according\s+to)"
# A: "<quote>," said <Name, title>.
_QUOTE_AFTER = re.compile(
    r"[“\"]([^”\"]{55,520}?)[”\"][,.]?\s+" + _SAY + r"\s+([A-Z][^.;]{2,110}?)[.;]", re.I)
# B: <Name, title>, said/stated, "<quote>".
_QUOTE_BEFORE = re.compile(
    r"([A-Z][A-Za-z.\-’']+(?:\s+[A-Z][A-Za-z.\-’']+){1,6}(?:,[^.\n“\"]{0,90}?)?),?\s+"
    + _SAY + r"[,:]?\s*[“\"]([^”\"]{55,520}?)[”\"]", re.I)


def extract_mgmt_quotes(press_text: str, limit: int = 3) -> list[dict]:
    """Verbatim leadership quotes from an earnings release. Handles both the
    'said <Name>' (quote-first) and '<Name>, <title>, said' (speaker-first) forms."""
    quotes: list[dict] = []
    seen: set[str] = set()

    def _add(quote: str, speaker: str) -> None:
        quote = _clean(quote)
        speaker = _clean(speaker).strip(",; ")
        key = quote[:50]
        if quote and 55 <= len(quote) <= 520 and key not in seen:
            seen.add(key)
            quotes.append({"quote": quote, "speaker": speaker})

    for m in _QUOTE_AFTER.finditer(press_text):
        _add(m.group(1), m.group(2))
        if len(quotes) >= limit:
            return quotes
    for m in _QUOTE_BEFORE.finditer(press_text):
        _add(m.group(2), m.group(1))
        if len(quotes) >= limit:
            break
    return quotes


# ---------------- source selection ----------------


def _latest_annual(ticker: str) -> tuple[str, str, str, str] | None:
    """(form, period_end, local_path, primary_doc_url) for newest 10-K/20-F."""
    with connect() as conn:
        row = conn.execute(
            "SELECT form, period_end, local_path, primary_doc_url FROM filings "
            "WHERE ticker=? AND filing_type IN ('10-K','20-F') AND status='ok' "
            "ORDER BY filed_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return tuple(row) if row else None


def _latest_earnings_text(ticker: str) -> tuple[str, str] | None:
    """(text, source_url) of the best earnings document: prefer an 8-K press
    release / 6-K content exhibit (from meta.json), else the primary doc."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT filing_type, local_path, primary_doc_url FROM filings "
            "WHERE ticker=? AND filing_type IN ('8-K-earnings','6-K') AND status='ok' "
            "ORDER BY filed_at DESC LIMIT 6",
            (ticker,),
        ).fetchall()
    pref = {"press_release": 0, "supplement": 1, "content": 2}
    for _ft, local_path, primary_url in rows:
        folder = (PROJECT_ROOT / local_path).parent
        meta_path = folder / "meta.json"
        chosen: Path | None = None
        src_url = primary_url
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:  # noqa: BLE001
                meta = {}
            exhibits = sorted(meta.get("exhibits", []),
                              key=lambda e: pref.get(e.get("kind"), 9))
            if exhibits:
                cand = folder / exhibits[0]["filename"]
                if cand.exists():
                    chosen, src_url = cand, exhibits[0].get("url", primary_url)
        if chosen is None:
            cand = PROJECT_ROOT / local_path
            chosen = cand if cand.exists() else None
        if chosen is None:
            continue
        text = html_to_text(chosen.read_bytes())
        quotes = extract_mgmt_quotes(text)
        if quotes:               # only worth using a doc that actually has quotes
            return text, src_url
    # Nothing with quotes — return the newest doc's text anyway (may yield none).
    if rows:
        cand = PROJECT_ROOT / rows[0][1]
        if cand.exists():
            return html_to_text(cand.read_bytes()), rows[0][2]
    return None


# ---------------- build + persist ----------------


def build_for_ticker(ticker: str) -> dict | None:
    annual = _latest_annual(ticker)
    payload: dict = {
        "self_description": None,
        "business_overview": None,
        "segments_note": None,
        "strategy_points": [],
        "risk_factors": [],
        "mgmt_quotes": [],
    }
    source_form = source_period = source_url = None

    if annual:
        source_form, source_period, local_path, source_url = annual
        text = html_to_text((PROJECT_ROOT / local_path).read_bytes())
        name = short_name(_NAME_BY_TICKER.get(ticker, ticker))
        section = extract_business_section(text, source_form, name)
        # Self-description + overview come from the cleanest business narrative we
        # can find: a full-text definitional anchor (Overview heading / '<Name>
        # is ...') beats the raw Item span, which often opens with a cautionary
        # or risk preamble. Fall back to the trimmed section, then the section.
        narrative = _anchored_section(text, name)
        if narrative is None and section:
            narrative = _trim_preamble(section, name)
        if narrative:
            payload["self_description"] = extract_self_description(narrative, name)
            payload["business_overview"] = extract_overview(narrative, payload["self_description"])
        if section:
            payload["strategy_points"] = extract_strategy_points(text, section)
        payload["segments_note"] = extract_segments_note(text)
        payload["risk_factors"] = extract_risk_factors(text, source_form)

    earnings = _latest_earnings_text(ticker)
    if earnings:
        etext, eurl = earnings
        payload["mgmt_quotes"] = extract_mgmt_quotes(etext)
        if payload["mgmt_quotes"]:
            payload["mgmt_quotes_source"] = eurl

    has_content = any([
        payload["self_description"], payload["business_overview"],
        payload["segments_note"], payload["mgmt_quotes"],
    ])
    if not has_content:
        return None
    payload["_source_form"] = source_form
    payload["_source_period"] = source_period
    payload["_source_url"] = source_url
    return payload


def save(ticker: str, payload: dict) -> None:
    init_schema()
    body = {k: v for k, v in payload.items() if not k.startswith("_")}
    with connect() as conn:
        conn.execute(
            "INSERT INTO filing_insights(ticker, source_form, source_period, source_url, "
            "generated_at, method, content_json) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET source_form=excluded.source_form, "
            "source_period=excluded.source_period, source_url=excluded.source_url, "
            "generated_at=excluded.generated_at, method=excluded.method, "
            "content_json=excluded.content_json",
            (
                ticker, payload.get("_source_form"), payload.get("_source_period"),
                payload.get("_source_url"),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                METHOD, json.dumps(body),
            ),
        )
        conn.commit()


def get(ticker: str) -> dict | None:
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT source_form, source_period, source_url, generated_at, method, content_json "
            "FROM filing_insights WHERE ticker=?",
            (ticker.upper(),),
        ).fetchone()
    if not row:
        return None
    return {
        "source_form": row[0], "source_period": row[1], "source_url": row[2],
        "generated_at": row[3], "method": row[4], **json.loads(row[5]),
    }


# ---------------- orchestration / CLI ----------------


def refresh_ticker(ticker: str) -> bool:
    t = ticker.upper()
    payload = build_for_ticker(t)
    if payload is None:
        print(f"  [skip] {t}: no parsable annual report or earnings text on disk")
        return False
    save(t, payload)
    sd = (payload["self_description"] or "")[:80]
    print(f"  [ok]   {t}: {payload['_source_form'] or '—'} "
          f"{payload['_source_period'] or ''}  risks={len(payload['risk_factors'])}  "
          f"quotes={len(payload['mgmt_quotes'])}  "
          f"{'“' + sd + '…”' if sd else '(no self-desc)'}")
    return True


def refresh_all() -> None:
    tickers = [c[0] for c in COMPANIES if c[0] not in BENCHMARK_TICKERS]
    print(f"Extracting filing insights for {len(tickers)} companies")
    ok = 0
    for t in tickers:
        ok += refresh_ticker(t)
    print(f"\n=== {ok} ok, {len(tickers) - ok} skipped ===")


def show(ticker: str) -> None:
    rec = get(ticker)
    if not rec:
        print(f"No filing insights for {ticker}.")
        return
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            "  python -m backend.filing_insights refresh <TICKER>\n"
            "  python -m backend.filing_insights refresh all\n"
            "  python -m backend.filing_insights show <TICKER>"
        )
        return
    cmd = args[0]
    if cmd == "refresh":
        if len(args) < 2:
            raise SystemExit("refresh needs a target")
        if args[1] == "all":
            refresh_all()
        else:
            refresh_ticker(args[1])
    elif cmd == "show":
        if len(args) < 2:
            raise SystemExit("show needs a TICKER")
        show(args[1])
    else:
        raise SystemExit(f"Unknown command: {cmd!r}")


if __name__ == "__main__":
    main()
