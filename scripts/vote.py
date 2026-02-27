#!/usr/bin/env python3
"""
OpenClaw skill script: record a user's vote on a proposal.

Usage:
  python3 scripts/vote.py --ticker TSLA --proposal 1 --vote FOR
  python3 scripts/vote.py --ticker TSLA --proposal 1 --vote AGAINST --note "Too dilutive"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db
from agent.decision_engine import DecisionEngine


def main():
    parser = argparse.ArgumentParser(description="Record a vote")
    parser.add_argument("--ticker", "-t", required=True)
    parser.add_argument("--proposal", "-p", required=True, type=int, help="Proposal number")
    parser.add_argument("--vote", "-v", required=True, choices=["FOR", "AGAINST", "ABSTAIN", "WITHHOLD"])
    parser.add_argument("--note", "-n", default="", help="Optional note")
    args = parser.parse_args()

    init_db()

    engine = DecisionEngine()
    queue = engine.get_review_queue()

    # Find the matching proposal in the review queue
    match = None
    for row in queue:
        if row["ticker"].upper() == args.ticker.upper() and row["proposal_number"] == args.proposal:
            match = row
            break

    if not match:
        # Try broader search — it might already be decided but user wants to override
        from data.storage import get_report_rows
        all_rows = get_report_rows(ticker=args.ticker.upper())
        for row in all_rows:
            if row["proposal_number"] == args.proposal:
                match = row
                break

    if not match:
        print(f"Could not find proposal #{args.proposal} for {args.ticker.upper()}.")
        print("Check the proposal number with the report command.")
        return

    decision_id = match.get("decision_id")
    if not decision_id:
        print(f"No AI analysis found for {args.ticker.upper()} #{args.proposal}. Run analyze first.")
        return

    engine.apply_user_vote(
        decision_id=decision_id,
        vote=args.vote,
        note=args.note,
        learn=True,
    )

    print(f"Recorded: *{args.vote}* on {args.ticker.upper()} proposal #{args.proposal}")
    if args.note:
        print(f"Note: _{args.note}_")


if __name__ == "__main__":
    main()
