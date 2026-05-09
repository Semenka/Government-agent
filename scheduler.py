"""
Scheduler — runs the governance pipeline automatically.

Three modes:

  1. python main.py digest          — one-shot weekly pipeline + digest
  2. python main.py meeting-check   — one-shot daily meeting fan-out
  3. python main.py schedule        — long-running daemon: weekly digest +
                                       daily meeting-check at MEETING_CHECK_TIME

The pipeline:
  fetch  → analyze (two-pass, batched)  → digest via active notifier channels

The meeting-check job runs once a day, finds proposals whose meeting falls in
{ALERT_TIERS} days from today, ensures a decision exists (analyzing on demand
if missing), and fans out to every active notifier — deduped per
(proposal, tier, channel) via the meeting_alerts table.

For production use you can also drive both via cron / launchd:

  # Mac launchd is documented in the README.
  # Crontab equivalent:
  0 8  * * 1  cd /path/to/Government-agent && python main.py digest
  0 7  * * *  cd /path/to/Government-agent && python main.py meeting-check
"""

import time
from datetime import datetime, timedelta

import schedule as sched_lib

from config import (
    SCHEDULE_TIME,
    SCHEDULE_DAY,
    EDGAR_TICKERS,
    PORTFOLIO,
    ALERT_TIERS,
    MEETING_CHECK_TIME,
)
from data.storage import init_db
from data.edgar import EDGARClient, extract_meeting_metadata
from agent.analyzer import ProposalAnalyzer
from agent.decision_engine import DecisionEngine
from data import storage
from notifiers import get_active_notifiers
from notifiers.base import AlertTier


# ---------------------------------------------------------------------------
# Weekly pipeline (fetch → analyze → digest fan-out)
# ---------------------------------------------------------------------------

def run_pipeline(verbose: bool = True) -> dict:
    """
    Execute the full governance pipeline:
      1. Fetch latest proxy filings from EDGAR for all US tickers
      2. Capture meeting metadata (meeting_date, vote_deadline, meeting_url)
      3. Analyze all pending proposals with two-pass critique
      4. Send the digest via every active notifier channel

    Returns a summary dict.
    """
    init_db()
    summary = {
        "fetched_filings": 0,
        "extracted_proposals": 0,
        "analyzed": 0,
        "auto_decided": 0,
        "escalated": 0,
        "revised_by_critique": 0,
        "errors": [],
        "channels_notified": [],
    }

    # ------------------------------------------------------------------
    # Step 1: Fetch + meeting metadata
    # ------------------------------------------------------------------
    if verbose:
        _banner("STEP 1: Fetching proxy filings from EDGAR")

    client = EDGARClient()
    analyzer = ProposalAnalyzer()

    for ticker in EDGAR_TICKERS:
        company = PORTFOLIO[ticker]
        cik = company["cik"]
        if verbose:
            print(f"\n  {ticker} ({company['name']}) …")

        try:
            is_fpi = company.get("is_foreign_private_issuer", False)
            filings = client.get_recent_proxy_filings(
                cik, months_back=6, is_foreign_private_issuer=is_fpi,
            )
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
                doc = client.download_document(url)
                metadata = extract_meeting_metadata(doc.text)
                proposals = client.extract_proposals_with_claude(doc.text, ticker, analyzer)
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
                    meeting_date=metadata.meeting_date,
                    filing_date=filing.filing_date,
                    accession_number=filing.accession_number,
                    vote_deadline=metadata.vote_deadline,
                    meeting_url=metadata.meeting_url,
                    extraction_truncated=doc.truncated,
                )

            if verbose and metadata.meeting_date:
                print(
                    f"    Meeting on {metadata.meeting_date}"
                    + (f" (deadline {metadata.vote_deadline})" if metadata.vote_deadline else "")
                )

    # ------------------------------------------------------------------
    # Step 2: Analyze
    # ------------------------------------------------------------------
    if verbose:
        _banner("STEP 2: Analyzing pending proposals (two-pass critique)")

    engine = DecisionEngine()
    report = engine.process_all_pending(verbose=verbose, two_pass=True)

    summary["analyzed"] = report.analyzed
    summary["auto_decided"] = report.auto_decided
    summary["escalated"] = report.escalated
    summary["revised_by_critique"] = report.revised_by_critique
    summary["errors"].extend(report.errors)

    # ------------------------------------------------------------------
    # Step 3: Digest fan-out
    # ------------------------------------------------------------------
    if verbose:
        _banner("STEP 3: Sending digest via active notifier channels")

    rows = [dict(r) for r in storage.get_report_rows()]
    week_label = _current_week_label()
    notifiers = get_active_notifiers()
    if not notifiers:
        print("  (No notifier channels are configured — set NOTIFIER_CHANNELS or "
              "TELEGRAM_BOT_TOKEN/SMTP_* in .env)")
    for ch in notifiers:
        ok = ch.send_digest(rows=rows, week_label=week_label)
        if ok:
            summary["channels_notified"].append(ch.name)
            if verbose:
                print(f"  ✓ Digest sent via {ch.name}")
        elif verbose:
            print(f"  ✗ Digest failed via {ch.name}")

    if verbose:
        _banner("PIPELINE COMPLETE")
        print(f"  Filings fetched: {summary['fetched_filings']}")
        print(f"  Proposals extracted: {summary['extracted_proposals']}")
        print(f"  Analyzed: {summary['analyzed']}")
        print(f"  Auto-decided: {summary['auto_decided']}")
        print(f"  Needs review: {summary['escalated']}")
        print(f"  Revised by critique: {summary['revised_by_critique']}")
        print(f"  Errors: {len(summary['errors'])}")
        print(f"  Channels notified: {', '.join(summary['channels_notified']) or 'none'}")
        print("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# Daily meeting-aware fan-out
# ---------------------------------------------------------------------------

def run_meeting_check(
    verbose: bool = True,
    dry_run: bool = False,
    only_tier: str | None = None,
) -> dict:
    """
    Find proposals whose meeting is N days away (N in ALERT_TIERS) and fire
    one alert per (proposal, tier, channel) — using the meeting_alerts table
    for dedupe so each tier fires exactly once.

    `dry_run=True` prints what would fire without calling the LLM or notifiers.
    `only_tier="T-7"` restricts to a single tier (for tests).
    """
    init_db()
    summary = {
        "considered": 0,
        "alerted": 0,
        "skipped_already_sent": 0,
        "analyzed_on_demand": 0,
        "errors": [],
    }

    tiers = [AlertTier(days_out=d, label=f"T-{d}") for d in ALERT_TIERS]
    if only_tier:
        tiers = [t for t in tiers if t.code == only_tier]

    # Look at proposals up to the largest tier window (e.g. T-14)
    max_window = max((t.days_out for t in tiers), default=0)
    proposals = storage.get_proposals_by_meeting_window(0, max_window)

    summary["considered"] = len(proposals)
    if verbose:
        _banner(
            f"MEETING CHECK — {len(proposals)} proposals within {max_window} days"
            + (" [DRY RUN]" if dry_run else "")
        )

    if not proposals:
        return summary

    today = datetime.now().date()
    engine = DecisionEngine()
    notifiers = get_active_notifiers() if not dry_run else []

    if not notifiers and not dry_run:
        print("  (No notifier channels configured — nothing will be sent)")

    for row in proposals:
        try:
            meeting = datetime.strptime(row["meeting_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        days_until = (meeting - today).days

        # Pick the tier whose days_out matches exactly. We don't fire a "T-7"
        # alert for a proposal sitting at T-6 — instead T-3 will catch it.
        # This keeps the dedupe key meaningful and avoids drift.
        tier = next((t for t in tiers if t.days_out == days_until), None)
        if not tier:
            continue

        if verbose:
            print(
                f"  [{tier.code}] {row['ticker']} #{row['proposal_number']} "
                f"meeting {row['meeting_date']} — {row['title'][:50]}"
            )

        if dry_run:
            summary["alerted"] += 1
            continue

        # Ensure we have a decision; analyze on demand if not.
        decision_row = storage.get_decision_for_proposal(row["id"])
        if decision_row is None:
            try:
                decision_id = engine.process_for_meeting(row["id"], two_pass=True)
                summary["analyzed_on_demand"] += 1
                decision_row = storage.get_decision_for_proposal(row["id"])
            except Exception as exc:
                err = f"analyze {row['ticker']} #{row['proposal_number']}: {exc}"
                summary["errors"].append(err)
                if verbose:
                    print(f"    ERROR: {err}")
                continue
        if decision_row is None:
            continue

        proposal_payload = dict(row)
        decision_payload = dict(decision_row)
        decision_payload["decision_id"] = decision_row["id"]

        for ch in notifiers:
            if storage.meeting_alert_exists(row["id"], tier.code, ch.name):
                summary["skipped_already_sent"] += 1
                continue
            try:
                msg_id = ch.send_alert(proposal_payload, decision_payload, tier)
                storage.record_meeting_alert(row["id"], tier.code, ch.name, msg_id or "")
                summary["alerted"] += 1
                if verbose:
                    print(f"    → sent via {ch.name} (msg_id={msg_id or '—'})")
            except Exception as exc:
                err = f"alert {row['ticker']} via {ch.name}: {exc}"
                summary["errors"].append(err)
                if verbose:
                    print(f"    ERROR: {err}")

    if verbose:
        print(
            f"\n  Considered: {summary['considered']}  "
            f"Alerts fired: {summary['alerted']}  "
            f"Already sent: {summary['skipped_already_sent']}  "
            f"On-demand analyses: {summary['analyzed_on_demand']}  "
            f"Errors: {len(summary['errors'])}"
        )

    return summary


# ---------------------------------------------------------------------------
# Long-running daemon
# ---------------------------------------------------------------------------

def run_daemon(verbose: bool = True) -> None:
    """
    Long-lived daemon that runs:
      - run_pipeline   weekly on SCHEDULE_DAY at SCHEDULE_TIME
      - run_meeting_check  every day at MEETING_CHECK_TIME

    Blocking call — intended to run under launchd / systemd / tmux.
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
    sched_lib.every().day.at(MEETING_CHECK_TIME).do(run_meeting_check, verbose=verbose)

    print(
        f"Scheduler running:\n"
        f"  Weekly digest: {SCHEDULE_DAY.capitalize()} at {SCHEDULE_TIME}\n"
        f"  Daily meeting-check: every day at {MEETING_CHECK_TIME}\n"
        f"Press Ctrl-C to stop.\n"
    )

    while True:
        sched_lib.run_pending()
        time.sleep(30)


def print_cron_instructions() -> None:
    """Print cron and launchd snippets for running the agent without the daemon."""
    import os
    cwd = os.getcwd()
    python = "python3"
    print(
        "Alternative to the daemon — cron:\n\n"
        "  crontab -e\n"
        f"  0 8  * * 1  cd {cwd} && {python} main.py digest         >> ~/governance.log 2>&1\n"
        f"  0 7  * * *  cd {cwd} && {python} main.py meeting-check  >> ~/governance.log 2>&1\n\n"
        "On macOS the README documents launchd plists for both jobs."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _current_week_label() -> str:
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%B %d, %Y")
