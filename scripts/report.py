#!/usr/bin/env python3
"""
OpenClaw skill script: print a plain-text voting report.

Usage:
  python3 scripts/report.py
  python3 scripts/report.py --ticker TSLA
  python3 scripts/report.py --year 2025
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db, get_report_rows
from config import PORTFOLIO


def main():
    parser = argparse.ArgumentParser(description="Voting report")
    parser.add_argument("--ticker", "-t", default=None)
    parser.add_argument("--year", "-y", default=None)
    args = parser.parse_args()

    init_db()
    rows = get_report_rows(ticker=args.ticker, year=args.year)

    if not rows:
        print("No proposals found." + (f" (filter: {args.ticker or ''} {args.year or ''})" if args.ticker or args.year else ""))
        return

    pending = sum(1 for r in rows if r["status"] == "pending")
    analyzed = sum(1 for r in rows if r["status"] != "pending")
    review_needed = sum(1 for r in rows if r["needs_review"] and not r["user_override"])

    print(f"*Governance Report*")
    print(f"Total: *{len(rows)}* proposals | Analyzed: *{analyzed}* | Pending: *{pending}* | Review needed: *{review_needed}*")
    print()

    # Group by ticker
    by_ticker: dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    for ticker, proposals in by_ticker.items():
        company = proposals[0]["company_name"]
        print(f"*{ticker}* ({company})")
        for r in proposals:
            final_vote = r["user_override"] or r["recommendation"] or "—"
            mgmt = r["management_rec"] or "—"
            conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"
            needs = r["needs_review"] and not r["user_override"]
            flag = " [REVIEW]" if needs else ""
            source = "You" if r["user_override"] else "AI"
            print(
                f"  #{r['proposal_number']}: {(r['title'] or '')[:45]}\n"
                f"    Vote: *{final_vote}* ({source}) | Mgmt: {mgmt} | Conf: {conf}{flag}"
            )
        print()

    if review_needed:
        print(f"_{review_needed} proposal(s) need your decision. Ask me to show review items._")


if __name__ == "__main__":
    main()
