#!/usr/bin/env python3
"""
OpenClaw skill script: run the full governance pipeline and print the digest.

Usage:
  python3 scripts/digest.py               # full pipeline: fetch -> analyze -> digest
  python3 scripts/digest.py --report-only  # skip fetch+analyze, just print digest
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta
from data.storage import init_db, get_report_rows
from notifications import _build_whatsapp_message


def main():
    parser = argparse.ArgumentParser(description="Governance digest")
    parser.add_argument(
        "--report-only", action="store_true",
        help="Skip fetch and analyze, just print the digest from existing data",
    )
    args = parser.parse_args()

    init_db()

    if not args.report_only:
        # Run the full pipeline (fetch + analyze) but suppress notification sending
        from scheduler import run_pipeline
        import notifications

        # Override to prevent the pipeline from trying to send notifications
        # (OpenClaw handles delivery — we just print to stdout)
        original_channel = notifications.NOTIFICATION_CHANNEL
        notifications.NOTIFICATION_CHANNEL = "none"
        try:
            run_pipeline(verbose=False, week_only=True)
        finally:
            notifications.NOTIFICATION_CHANNEL = original_channel

    rows = get_report_rows()

    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    week_label = monday.strftime("%B %d, %Y")

    message = _build_whatsapp_message(rows, week_label)
    print(message)


if __name__ == "__main__":
    main()
