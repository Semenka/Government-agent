#!/usr/bin/env python3
"""
OpenClaw skill script: fetch proxy filings from SEC EDGAR.

Usage:
  python3 scripts/fetch.py
  python3 scripts/fetch.py --ticker TSLA
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EDGAR_TICKERS, MANUAL_TICKERS, PORTFOLIO
from data.storage import init_db, upsert_proposal
from data.edgar import EDGARClient
from agent.analyzer import ProposalAnalyzer


def main():
    parser = argparse.ArgumentParser(description="Fetch proxy filings")
    parser.add_argument("--ticker", "-t", default=None)
    parser.add_argument("--months", "-m", type=int, default=18)
    args = parser.parse_args()

    init_db()

    tickers = [args.ticker.upper()] if args.ticker else EDGAR_TICKERS

    client = EDGARClient()
    analyzer = ProposalAnalyzer()
    total_filings = 0
    total_proposals = 0

    for t in tickers:
        if t not in PORTFOLIO:
            print(f"Unknown ticker: {t}")
            continue
        if t in MANUAL_TICKERS:
            print(f"{t} is international — needs manual entry, skipping.")
            continue

        company = PORTFOLIO[t]
        cik = company["cik"]
        print(f"Fetching *{t}* ({company['name']})...")

        is_fpi = company.get("is_foreign_private_issuer", False)
        try:
            filings = client.get_recent_proxy_filings(
                cik, months_back=args.months, is_foreign_private_issuer=is_fpi,
            )
        except Exception as exc:
            print(f"  Failed: {exc}")
            continue

        if not filings:
            print(f"  No recent filings found.")
            continue

        total_filings += len(filings)
        print(f"  Found {len(filings)} filing(s).")

        for filing in filings:
            url = client.get_filing_document_url(filing)
            if not url:
                continue
            try:
                text = client.download_document_text(url)
                proposals = client.extract_proposals_with_claude(text, t, analyzer)
            except Exception as exc:
                print(f"  Parse error: {exc}")
                continue

            total_proposals += len(proposals)
            for p in proposals:
                upsert_proposal(
                    ticker=t,
                    company_name=company["name"],
                    proposal_number=p.proposal_number,
                    title=p.title,
                    full_text=p.description,
                    management_rec=p.management_recommendation,
                    proposal_type="other",
                    source="edgar",
                    meeting_date="",
                    filing_date=filing.filing_date,
                    accession_number=filing.accession_number,
                )
                print(f"  #{p.proposal_number}: {p.title[:50]}")

    print(f"\nDone. *{total_filings}* filings, *{total_proposals}* proposals extracted.")
    if total_proposals:
        print("_Ask me to analyze proposals next._")


if __name__ == "__main__":
    main()
