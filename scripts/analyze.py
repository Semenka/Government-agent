#!/usr/bin/env python3
"""
OpenClaw skill script: run AI analysis on pending proposals.

Usage:
  python3 scripts/analyze.py
  python3 scripts/analyze.py --ticker NVDA
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db
from agent.decision_engine import DecisionEngine


def main():
    parser = argparse.ArgumentParser(description="Analyze proposals")
    parser.add_argument("--ticker", "-t", default=None)
    args = parser.parse_args()

    init_db()

    engine = DecisionEngine()
    report = engine.process_all_pending(ticker=args.ticker, verbose=False)

    print(f"*Analysis Complete*")
    print(f"Analyzed: *{report.analyzed}* / {report.total}")
    print(f"Auto-decided: *{report.auto_decided}*")
    print(f"Needs review: *{report.escalated}*")

    if report.errors:
        print(f"\nErrors ({len(report.errors)}):")
        for e in report.errors:
            print(f"  - {e}")

    if report.escalated:
        print(f"\n_{report.escalated} proposal(s) need your decision. Ask me to show review items._")


if __name__ == "__main__":
    main()
