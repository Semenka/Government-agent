"""
SEC EDGAR API client for fetching DEF 14A proxy filings.

Optimizations over v1:
  - Disk-based response caching (avoids re-downloading proxy statements)
  - Retry with exponential backoff for network errors
  - Connection pooling via requests.Session
  - Meeting-date / vote-deadline / meeting-URL extraction from proxy cover pages
"""

import hashlib
import os
import re
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import EDGAR_USER_AGENT, EDGAR_HTML_MAX_CHARS, EDGAR_CACHE_DIR, PORTFOLIO

# Form types to look for when fetching proxy-related filings.
# US domestic issuers file DEF 14A / DEFA14A.
# Foreign private issuers (e.g. TTE) file 6-K which may contain AGM/proxy materials.
PROXY_FORM_TYPES_DOMESTIC = {"DEF 14A", "DEFA14A"}
PROXY_FORM_TYPES_FOREIGN = {"6-K", "6-K/A"}


@dataclass
class Filing:
    accession_number: str
    filing_date: str
    form_type: str
    primary_document: str
    cik: str


@dataclass
class RawProposal:
    proposal_number: str
    title: str
    description: str
    management_recommendation: str


@dataclass
class MeetingMetadata:
    meeting_date: str = ""        # ISO YYYY-MM-DD
    vote_deadline: str = ""       # ISO YYYY-MM-DD (typically meeting_date - 1 business day if not stated)
    meeting_url: str = ""         # URL to meeting/notice page if present in filing


@dataclass
class FetchedDocument:
    text: str
    truncated: bool = False
    raw_length: int = 0


# ---------------------------------------------------------------------------
# Disk cache helpers
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> Path:
    """Return a deterministic cache file path for a URL."""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return Path(EDGAR_CACHE_DIR) / f"{h}.txt"


def _read_cache(url: str) -> str | None:
    """Return cached content for a URL, or None if not cached."""
    p = _cache_path(url)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def _write_cache(url: str, content: str) -> None:
    """Write content to the disk cache."""
    p = _cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class EDGARClient:
    BASE = "https://data.sec.gov"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": EDGAR_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Rate-limited GET with retry + exponential backoff
    # ------------------------------------------------------------------
    def _get(self, url: str, retries: int = 3, **kwargs) -> requests.Response:
        # Rate limit: SEC allows 10 req/s; we stay at ~3 req/s
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.35:
            time.sleep(0.35 - elapsed)
        self._last_request_time = time.time()

        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                else:
                    raise
            except requests.HTTPError as exc:
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                else:
                    raise

    # ------------------------------------------------------------------
    # Filing discovery
    # ------------------------------------------------------------------

    def get_recent_proxy_filings(
        self,
        cik: str,
        months_back: int = 18,
        is_foreign_private_issuer: bool = False,
    ) -> list[Filing]:
        """
        Return proxy-related filings from the past `months_back` months.

        For domestic issuers: looks for DEF 14A / DEFA14A.
        For foreign private issuers (e.g. TTE): looks for 6-K filings
        that may contain AGM or proxy materials.
        """
        cik_padded = cik.lstrip("0").zfill(10)
        url = f"{self.BASE}/submissions/CIK{cik_padded}.json"

        # Check JSON cache
        cached = _read_cache(url)
        if cached:
            import json
            data = json.loads(cached)
        else:
            resp = self._get(url)
            data = resp.json()
            _write_cache(url, resp.text)

        cutoff = datetime.now() - timedelta(days=months_back * 30)
        filings: list[Filing] = []

        target_forms = (
            PROXY_FORM_TYPES_FOREIGN if is_foreign_private_issuer
            else PROXY_FORM_TYPES_DOMESTIC
        )

        recent = data.get("filings", {}).get("recent", {})
        form_types = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for i, form in enumerate(form_types):
            if form not in target_forms:
                continue
            date_str = filing_dates[i]
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if date < cutoff:
                continue
            filings.append(Filing(
                accession_number=accessions[i],
                filing_date=date_str,
                form_type=form,
                primary_document=primary_docs[i] if i < len(primary_docs) else "",
                cik=cik_padded,
            ))

        return filings

    def get_filing_document_url(self, filing: Filing) -> str | None:
        """Return the URL of the primary proxy statement HTML document."""
        accession_clean = filing.accession_number.replace("-", "")
        cik_int = int(filing.cik)

        # Try to get the filing index
        index_url = (
            f"{self.BASE}/Archives/edgar/data/{cik_int}/"
            f"{accession_clean}/{filing.accession_number}-index.json"
        )
        try:
            resp = self._get(index_url)
            data = resp.json()
            for doc in data.get("directory", {}).get("item", []):
                name = doc.get("name", "")
                if name.endswith((".htm", ".html")) and "def14a" in name.lower():
                    return (
                        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                        f"{accession_clean}/{name}"
                    )
        except Exception:
            pass

        # Fallback: use primary_document directly
        if filing.primary_document:
            return (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{accession_clean}/{filing.primary_document}"
            )
        return None

    def download_document(self, url: str) -> FetchedDocument:
        """
        Download an HTML proxy statement, cache it, and return the cleaned text
        along with a flag indicating whether the configured cap truncated it.

        The cache stores the raw cleaned text (untruncated) keyed by URL hash.
        Truncation happens at read time so that bumping EDGAR_HTML_MAX_CHARS
        in config doesn't require purging the cache.
        """
        cached = _read_cache(url)
        if cached is not None:
            text = cached
        else:
            headers = {
                "User-Agent": EDGAR_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "head"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            _write_cache(url, text)

        raw_length = len(text)
        truncated = raw_length > EDGAR_HTML_MAX_CHARS
        if truncated:
            text = text[:EDGAR_HTML_MAX_CHARS]
        return FetchedDocument(text=text, truncated=truncated, raw_length=raw_length)

    def download_document_text(self, url: str) -> str:
        """Backwards-compatible string-only fetch. Prefer download_document()."""
        return self.download_document(url).text

    # ------------------------------------------------------------------
    # Proposal extraction (delegates to the analyzer)
    # ------------------------------------------------------------------

    def extract_proposals_with_claude(
        self,
        text: str,
        ticker: str,
        analyzer,
    ) -> list[RawProposal]:
        """Use the LLM to pull ballot items out of raw proxy text."""
        return analyzer.extract_ballot_items(text, ticker)


# ---------------------------------------------------------------------------
# Meeting metadata extraction (regex-first, LLM fallback)
# ---------------------------------------------------------------------------

_MONTH = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)

# Allow "annual general meeting", "annual shareholders meeting", "annual meeting
# of stockholders", etc. — any words may sit between "annual"/"special" and
# the literal "meeting".
_MEETING_PHRASE = r"(?:annual|special)\s+(?:[\w\-]+\s+){0,3}meeting"
_HELD_PHRASE = r"(?:will\s+be\s+held|to\s+be\s+held|is\s+to\s+be\s+held|held|convened)\s+(?:on\s+)?"

# Order matters: more-specific patterns first so we don't match a record-date
# or filing-date string by accident.
_MEETING_DATE_PATTERNS = [
    # "Annual Meeting … will be held on May 23, 2026"
    re.compile(
        rf"{_MEETING_PHRASE}[^\n]{{0,200}}?{_HELD_PHRASE}"
        rf"((?:{_MONTH})\s+\d{{1,2}},?\s+\d{{4}})",
        re.IGNORECASE,
    ),
    # "Meeting date: May 23, 2026" / "Date of the meeting: ..."
    re.compile(
        rf"(?:meeting\s+date|date\s+of\s+(?:the\s+)?meeting)\s*[:\-]?\s*"
        rf"((?:{_MONTH})\s+\d{{1,2}},?\s+\d{{4}})",
        re.IGNORECASE,
    ),
    # European/UK form: "… will be held on 23 May 2026"
    re.compile(
        rf"{_MEETING_PHRASE}[^\n]{{0,200}}?{_HELD_PHRASE}"
        rf"(\d{{1,2}}\s+(?:{_MONTH})\s+\d{{4}})",
        re.IGNORECASE,
    ),
    # Numeric form: "… will be held on 5/23/2026"
    re.compile(
        rf"{_MEETING_PHRASE}[^\n]{{0,200}}?{_HELD_PHRASE}"
        r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
        re.IGNORECASE,
    ),
]

_RECORD_DATE_PATTERN = re.compile(
    r"record\s+date[^.\n]{0,40}?"
    r"((?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
    re.IGNORECASE,
)

_VOTE_DEADLINE_PATTERN = re.compile(
    rf"(?:proxy|proxies|vote|votes|ballot|ballots)\s+"
    r"(?:must\s+be\s+received|must\s+arrive|"
    r"received\s+by|are\s+due\s+by|due\s+by|deadline|cutoff)"
    rf"[^\n]{{0,120}}?"
    rf"((?:{_MONTH})\s+\d{{1,2}},?\s+\d{{4}}"
    r"|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}"
    rf"|\d{{1,2}}\s+(?:{_MONTH})\s+\d{{4}})",
    re.IGNORECASE,
)

_MEETING_URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']*(?:proxyvote|proxydocs|annualmeeting|"
    r"investor[^\s]*meeting|meeting[^\s]*notice|annual-meeting)"
    r"[^\s<>\"']*",
    re.IGNORECASE,
)

_DATE_FORMATS = [
    "%B %d, %Y", "%B %d %Y", "%d %B %Y",
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
    "%Y-%m-%d",
]


def _normalize_date(raw: str) -> str:
    """Parse a date string in any of the formats above into ISO YYYY-MM-DD.
    Returns "" if parsing fails."""
    cleaned = raw.strip().rstrip(".,").replace(",", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def extract_meeting_metadata(text: str) -> MeetingMetadata:
    """
    Pull meeting_date, vote_deadline, and meeting_url out of proxy text.

    Regex-first because cover-page phrasing is highly conventional. Callers can
    fall back to an LLM for fuzzy filings that don't match — see analyzer's
    extract_ballot_items prompt, which can be extended to ask for these fields too.
    """
    md = MeetingMetadata()

    head = text[:8000]  # Cover page + early body — enough for almost all proxies

    for pat in _MEETING_DATE_PATTERNS:
        m = pat.search(head)
        if m:
            iso = _normalize_date(m.group(1))
            if iso:
                md.meeting_date = iso
                break

    m = _VOTE_DEADLINE_PATTERN.search(head)
    if m:
        iso = _normalize_date(m.group(1))
        if iso:
            md.vote_deadline = iso

    m = _MEETING_URL_PATTERN.search(text)
    if m:
        md.meeting_url = m.group(0).rstrip(".,;:")

    # Default vote_deadline to meeting_date if we know the meeting date
    # but no explicit cutoff was found. Better to alert too early than too late.
    if md.meeting_date and not md.vote_deadline:
        md.vote_deadline = md.meeting_date

    return md
