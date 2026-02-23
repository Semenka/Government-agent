"""
Scheduler — runs the governance pipeline automatically and sends a digest email.

Two modes:
  1. `python main.py digest`      — run the pipeline once now and send email
  2. `python main.py schedule`    — run as a daemon, trigger every Monday morning

The pipeline:  fetch → analyze → email digest.

For production use you can also set up a cron job instead of the daemon:
  0 8 * * 1  cd /path/to/Government-agent && python main.py digest
"""

import time
from datetime import datetime, timedelta

import schedule as sched_lib

from config import (
    SCHEDULE_TIME,
    SCHEDULE_DAY,
    EDGAR_TICKERS,
    PORTFOLIO,
)
from data.storage import init_db
from data.edgar import EDGARClient
from agent.analyzer import ProposalAnalyzer
from agent.decision_engine import DecisionEngine
from data import storage
from notifications import send_digest_email


def run_pipeline(verbose: bool = True) -> dict:
    """
    Execute the full governance pipeline:
      1. Fetch latest proxy filings from EDGAR for all US tickers
      2. Analyze all pending proposals
      3. Send the digest email

    Returns a summary dict.
    """
    init_db()
    summary = {
        "fetched_filings": 0,
        "extracted_proposals": 0,
        "analyzed": 0,
        "auto_decided": 0,
        "escalated": 0,
        "errors": [],
        "email_sent": False,
    }

    # ------------------------------------------------------------------
    # Step 1: Fetch
    # ------------------------------------------------------------------
    if verbose:
        print("=" * 60)
        print("STEP 1: Fetching proxy filings from EDGAR")
        print("=" * 60)

    client = EDGARClient()
    analyzer = ProposalAnalyzer()

    for ticker in EDGAR_TICKERS:
        company = PORTFOLIO[ticker]
        cik = company["cik"]
        if verbose:
            print(f"\n  {ticker} ({company['name']}) …")

        try:
            filings = client.get_recent_proxy_filings(cik, months_back=6)
        except Exception as exc:
            summary["errors"].append(f"Fetch {ticker}: {exc}")
            if verbose:
                print(f"    Failed: {exc}")
            continue

        if not filings:
            if verbose:
                print("    No recent filings.")
            continue

        summary["fetched_filings"] += len(filings)
        for filing in filings:
            url = client.get_filing_document_url(filing)
            if not url:
                continue
            try:
                text = client.download_document_text(url)
                proposals = client.extract_proposals_with_claude(text, ticker, analyzer)
            except Exception as exc:
                summary["errors"].append(f"Parse {ticker}/{filing.accession_number}: {exc}")
                continue

            summary["extracted_proposals"] += len(proposals)
            for p in proposals:
                storage.upsert_proposal(
                    ticker=ticker,
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

    # ------------------------------------------------------------------
    # Step 2: Analyze
    # ------------------------------------------------------------------
    if verbose:
        print("\n" + "=" * 60)
        print("STEP 2: Analyzing pending proposals")
        print("=" * 60)

    engine = DecisionEngine()
    report = engine.process_all_pending(verbose=verbose)

    summary["analyzed"] = report.analyzed
    summary["auto_decided"] = report.auto_decided
    summary["escalated"] = report.escalated
    summary["errors"].extend(report.errors)

    # ------------------------------------------------------------------
    # Step 3: Email digest
    # ------------------------------------------------------------------
    if verbose:
        print("\n" + "=" * 60)
        print("STEP 3: Sending digest email")
        print("=" * 60)

    rows = storage.get_report_rows()
    summary["email_sent"] = send_digest_email(rows=rows)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    if verbose:
        print("\n" + "=" * 60)
        print("PIPELINE COMPLETE")
        print(f"  Filings fetched: {summary['fetched_filings']}")
        print(f"  Proposals extracted: {summary['extracted_proposals']}")
        print(f"  Analyzed: {summary['analyzed']}")
        print(f"  Auto-decided: {summary['auto_decided']}")
        print(f"  Needs review: {summary['escalated']}")
        print(f"  Errors: {len(summary['errors'])}")
        print(f"  Email sent: {summary['email_sent']}")
        print("=" * 60)

    return summary


def run_daemon(verbose: bool = True) -> None:
    """
    Run as a long-lived daemon process. Triggers the pipeline on the configured
    day and time (default: Monday 08:00).

    Blocking call — intended to be run via systemd, Docker, tmux, etc.
    """
    day_map = {
        "monday": sched_lib.every().monday,
        "tuesday": sched_lib.every().tuesday,
        "wednesday": sched_lib.every().wednesday,
        "thursday": sched_lib.every().thursday,
        "friday": sched_lib.every().friday,
        "saturday": sched_lib.every().saturday,
        "sunday": sched_lib.every().sunday,
    }

    day_fn = day_map.get(SCHEDULE_DAY.lower(), sched_lib.every().monday)
    day_fn.at(SCHEDULE_TIME).do(run_pipeline, verbose=verbose)

    print(
        f"Scheduler running. Pipeline will execute every "
        f"{SCHEDULE_DAY.capitalize()} at {SCHEDULE_TIME}."
    )
    print("Press Ctrl-C to stop.\n")

    while True:
        sched_lib.run_pending()
        time.sleep(30)


def print_cron_instructions() -> None:
    """Print instructions for setting up a cron job as an alternative to the daemon."""
    import os
    cwd = os.getcwd()
    python = "python3"
    print(
        "To run the digest automatically via cron instead of the daemon:\n\n"
        "  crontab -e\n\n"
        "Add this line (Monday 8:00 AM):\n\n"
        f"  0 8 * * 1 cd {cwd} && {python} main.py digest >> /var/log/governance-agent.log 2>&1\n\n"
        "Or for a different schedule, adjust the cron expression."
    )
