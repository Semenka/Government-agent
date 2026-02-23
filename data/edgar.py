"""
SEC EDGAR API client for fetching DEF 14A proxy filings.

Optimizations over v1:
  - Disk-based response caching (avoids re-downloading proxy statements)
  - Retry with exponential backoff for network errors
  - Connection pooling via requests.Session
"""

import hashlib
import os
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
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

    def download_document_text(self, url: str) -> str:
        """Download an HTML proxy statement, cache it, return cleaned text."""
        cached = _read_cache(url)
        if cached:
            return cached

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
        text = text[:EDGAR_HTML_MAX_CHARS]

        _write_cache(url, text)
        return text

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
