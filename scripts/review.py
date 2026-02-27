#!/usr/bin/env python3
"""
OpenClaw skill script: show proposals that need human review.

Usage:
  python3 scripts/review.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db
from agent.decision_engine import DecisionEngine


def main():
    init_db()

    engine = DecisionEngine()
    queue = engine.get_review_queue()

    if not queue:
        print("No proposals need your review right now.")
        return

    print(f"*{len(queue)} proposal(s) need your decision:*")
    print()

    for i, row in enumerate(queue, 1):
        concerns = json.loads(row["governance_concerns"] or "[]")
        conflicts = json.loads(row["conflicting_factors"] or "[]")

        print(f"*{i}. {row['ticker']}* — {row['company_name']}")
        print(f"   Meeting: {row['meeting_date'] or 'unknown'}")
        print(f"   Proposal #{row['proposal_number']}: *{row['title']}*")
        print(f"   Type: {row['proposal_type']}")
        print()

        text = (row["full_text"] or "")[:300]
        if text:
            print(f"   _{text}_")
            print()

        print(f"   AI recommendation: *{row['recommendation']}*")
        print(f"   Confidence: {row['confidence']:.0%} | Importance: {row['importance']:.0%}")

        value_impact = row.get("value_impact") or ""
        if value_impact:
            print(f"   Value impact: {value_impact[:150]}")

        reasoning = row.get("reasoning") or ""
        if reasoning:
            print(f"   Reasoning: {reasoning[:200]}")

        if concerns:
            print(f"   Governance concerns: {', '.join(concerns[:3])}")
        if conflicts:
            print(f"   Uncertainty factors: {', '.join(conflicts[:3])}")

        print()

    print("To vote, tell me: _vote FOR/AGAINST/ABSTAIN on [TICKER] proposal #[N]_")


if __name__ == "__main__":
    main()
