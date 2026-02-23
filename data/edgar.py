"""
SEC EDGAR API client for fetching DEF 14A proxy filings.

The SEC EDGAR free REST API is used:
  https://data.sec.gov/submissions/CIK{cik}.json   — filing history
  https://data.sec.gov/Archives/...                 — document downloads

Per SEC policy the request must include a descriptive User-Agent header.
Set EDGAR_USER_AGENT in config.py to your contact info.
"""

import time
from datetime import datetime, timedelta
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from config import EDGAR_USER_AGENT, EDGAR_HTML_MAX_CHARS


@dataclass
class Filing:
    accession_number: str
    filing_date: str
    form_type: str
    primary_document: str   # filename of the primary document in the filing
    cik: str


@dataclass
class RawProposal:
    proposal_number: str
    title: str
    description: str
    management_recommendation: str


class EDGARClient:
    BASE = "https://data.sec.gov"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": EDGAR_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        })
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Rate-limit: EDGAR asks for max 10 req/s; we keep it at ~3 req/s
    # ------------------------------------------------------------------
    def _get(self, url: str, **kwargs) -> requests.Response:
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.35:
            time.sleep(0.35 - elapsed)
        self._last_request_time = time.time()

        # Update host header for non-data.sec.gov domains
        if "efts.sec.gov" in url:
            self.session.headers["Host"] = "efts.sec.gov"
        elif "www.sec.gov" in url:
            self.session.headers["Host"] = "www.sec.gov"
        else:
            self.session.headers["Host"] = "data.sec.gov"

        resp = self.session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Filing discovery
    # ------------------------------------------------------------------

    def get_recent_proxy_filings(self, cik: str, months_back: int = 18) -> list[Filing]:
        """Return DEF 14A filings from the past `months_back` months."""
        cik_padded = cik.lstrip("0").zfill(10)
        url = f"{self.BASE}/submissions/CIK{cik_padded}.json"
        data = self._get(url).json()

        cutoff = datetime.now() - timedelta(days=months_back * 30)
        filings: list[Filing] = []

        recent = data.get("filings", {}).get("recent", {})
        form_types = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for i, form in enumerate(form_types):
            if form not in ("DEF 14A", "DEFA14A"):
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
                primary_document=primary_docs[i] if primary_docs else "",
                cik=cik_padded,
            ))

        return filings

    def get_filing_document_url(self, filing: Filing) -> str | None:
        """Return the URL of the primary proxy statement HTML document."""
        accession_clean = filing.accession_number.replace("-", "")
        index_url = (
            f"{self.BASE}/Archives/edgar/data/{int(filing.cik)}/"
            f"{accession_clean}/{filing.accession_number}-index.json"
        )
        try:
            data = self._get(index_url).json()
        except Exception:
            # Fall back: construct URL directly from primary_document
            if filing.primary_document:
                return (
                    f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/"
                    f"{accession_clean}/{filing.primary_document}"
                )
            return None

        # Find the primary HTML document
        for doc in data.get("documents", []):
            if doc.get("type") in ("DEF 14A", "DEFA14A") and doc.get("document", "").endswith((".htm", ".html")):
                return (
                    f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/"
                    f"{accession_clean}/{doc['document']}"
                )
        # Fallback to primary_document
        if filing.primary_document:
            return (
                f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/"
                f"{accession_clean}/{filing.primary_document}"
            )
        return None

    def download_document_text(self, url: str) -> str:
        """Download an HTML proxy statement and return cleaned text."""
        # Use a separate session for sec.gov (different host)
        headers = {
            "User-Agent": EDGAR_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        }
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove scripts and styles
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:EDGAR_HTML_MAX_CHARS]

    # ------------------------------------------------------------------
    # Proposal extraction (delegates to the Claude analyzer)
    # ------------------------------------------------------------------

    def extract_proposals_with_claude(
        self,
        text: str,
        ticker: str,
        analyzer,  # agent.analyzer.ProposalAnalyzer
    ) -> list[RawProposal]:
        """Use Claude to pull ballot items out of raw proxy text."""
        return analyzer.extract_ballot_items(text, ticker)
