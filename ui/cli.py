"""
Click + Rich CLI for the governance agent.

Commands:
  fetch        — pull DEF 14A filings from SEC EDGAR (US tickers)
  add          — manually enter ballot items (intl. companies)
  analyze      — run AI analysis on pending proposals
  review       — interactive queue for items needing human decision
  report       — print voting report table
  preferences  — list | set | learn
  chat         — freeform Q&A about governance
  digest       — run full pipeline once (fetch→analyze→email)
  schedule     — run as daemon, auto-trigger every Monday morning
  setup-gemini — verify Gemini API key and test the model
  setup-local  — verify local LLM (gbrain / LM Studio / …)
  setup-ollama — verify Ollama is running and list available models
"""

import json
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VOTE_COLOR = {"FOR": "green", "AGAINST": "red", "ABSTAIN": "yellow", "WITHHOLD": "yellow"}


def _ensure_db() -> None:
    from data.storage import init_db
    init_db()


def _check_llm_backend() -> None:
    """Verify the configured LLM backend has the credentials it needs."""
    backend_type = os.getenv("LLM_BACKEND", "anthropic").lower()
    if backend_type == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            console.print(
                "[bold red]Error:[/bold red] ANTHROPIC_API_KEY not set. "
                "Copy .env.example to .env and add your key, or switch backends."
            )
            sys.exit(1)
    elif backend_type == "gemini":
        if not os.getenv("GEMINI_API_KEY", ""):
            console.print(
                "[bold red]Error:[/bold red] GEMINI_API_KEY not set. "
                "Get one at https://aistudio.google.com/apikey\n"
                "Run [bold]python main.py setup-gemini[/bold] for help."
            )
            sys.exit(1)
    elif backend_type == "local":
        if not os.getenv("LOCAL_LLM_URL", ""):
            console.print(
                "[bold red]Error:[/bold red] LOCAL_LLM_URL not set. "
                "Point it at your local LLM server, e.g.\n"
                "  LOCAL_LLM_URL=http://127.0.0.1:1234/v1\n"
                "  LOCAL_LLM_MODEL=your-model-name\n"
                "Run [bold]python main.py setup-local[/bold] to verify the connection."
            )
            sys.exit(1)
    elif backend_type == "ollama":
        if not os.getenv("OLLAMA_URL", "") and not os.getenv("OLLAMA_MODEL", ""):
            console.print(
                "[yellow]Warning:[/yellow] OLLAMA_URL/OLLAMA_MODEL not set — "
                "using defaults http://127.0.0.1:11434 and llama3.1:8b."
            )


def _vote_cell(vote: str | None) -> Text:
    if not vote:
        return Text("—", style="dim")
    color = VOTE_COLOR.get(vote, "white")
    return Text(vote, style=f"bold {color}")


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@click.command()
@click.option("--ticker", "-t", default=None, help="Fetch only this ticker (default: all EDGAR tickers)")
@click.option("--months", "-m", default=18, show_default=True, help="How many months back to look")
def fetch(ticker: str | None, months: int) -> None:
    """Fetch DEF 14A proxy filings from SEC EDGAR for US portfolio companies."""
    _ensure_db()
    _check_llm_backend()

    from config import EDGAR_TICKERS, MANUAL_TICKERS, PORTFOLIO
    from data.edgar import EDGARClient
    from agent.analyzer import ProposalAnalyzer
    from data.storage import upsert_proposal

    tickers = [ticker.upper()] if ticker else EDGAR_TICKERS

    for t in tickers:
        if t not in PORTFOLIO:
            console.print(f"[yellow]Unknown ticker {t}, skipping.[/yellow]")
            continue
        if t in MANUAL_TICKERS:
            console.print(
                f"[yellow]{t} is an international company — use [bold]add[/bold] command instead.[/yellow]"
            )
            continue

        company = PORTFOLIO[t]
        cik = company["cik"]
        console.print(f"\n[bold]{t}[/bold] ({company['name']}) — fetching filings …")

        client_edgar = EDGARClient()
        analyzer = ProposalAnalyzer()

        is_fpi = company.get("is_foreign_private_issuer", False)
        try:
            filings = client_edgar.get_recent_proxy_filings(
                cik, months_back=months, is_foreign_private_issuer=is_fpi,
            )
        except Exception as exc:
            console.print(f"  [red]Failed to fetch filings: {exc}[/red]")
            continue

        if not filings:
            console.print("  No DEF 14A filings found in that period.")
            continue

        console.print(f"  Found {len(filings)} filing(s).")
        for filing in filings:
            console.print(
                f"  → {filing.form_type} filed {filing.filing_date} "
                f"({filing.accession_number})"
            )
            url = client_edgar.get_filing_document_url(filing)
            if not url:
                console.print("    [yellow]Could not resolve document URL — skipping.[/yellow]")
                continue

            console.print(f"    Downloading: {url[:80]}…")
            try:
                text = client_edgar.download_document_text(url)
            except Exception as exc:
                console.print(f"    [red]Download failed: {exc}[/red]")
                continue

            console.print("    Extracting ballot items via LLM …")
            try:
                proposals = client_edgar.extract_proposals_with_claude(text, t, analyzer)
            except Exception as exc:
                console.print(f"    [red]Extraction failed: {exc}[/red]")
                continue

            if not proposals:
                console.print("    [yellow]No ballot items found in this filing.[/yellow]")
                continue

            console.print(f"    Extracted {len(proposals)} ballot item(s):")
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
                console.print(f"      #{p.proposal_number}: {p.title[:60]}")

    console.print("\n[green]Fetch complete.[/green] Run [bold]analyze[/bold] next.")


# ---------------------------------------------------------------------------
# add  (manual entry for international companies)
# ---------------------------------------------------------------------------

@click.command("add")
@click.option("--ticker", "-t", default=None, help="Ticker (e.g. TTE, WISE, 1810.HK)")
def add_proposal(ticker: str | None) -> None:
    """Manually enter ballot items for WISE or 1810.HK (or any ticker)."""
    _ensure_db()

    from config import MANUAL_TICKERS, PORTFOLIO
    from data.manual_input import get_ir_hint, prompt_proposal_entry, save_manual_proposal

    if ticker:
        tickers = [ticker.upper()]
    else:
        tickers = MANUAL_TICKERS
        console.print(
            "[bold]International companies (manual entry):[/bold] "
            + ", ".join(tickers)
        )

    for t in tickers:
        if t not in PORTFOLIO:
            console.print(f"[yellow]Unknown ticker {t}.[/yellow]")
            continue
        console.print(f"\n[dim]{get_ir_hint(t)}[/dim]")

        while True:
            fields = prompt_proposal_entry(t)
            if not fields:
                break
            pid = save_manual_proposal(fields)
            console.print(
                f"  [green]Saved[/green] #{fields['proposal_number']}: {fields['title']} (id={pid})"
            )
            if not Confirm.ask("Add another proposal for this company?", default=False):
                break


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@click.command()
@click.option("--ticker", "-t", default=None, help="Analyze only this ticker")
def analyze(ticker: str | None) -> None:
    """Run AI analysis on all pending ballot proposals."""
    _ensure_db()
    _check_llm_backend()

    from agent.decision_engine import DecisionEngine

    backend_name = os.getenv("LLM_BACKEND", "anthropic")
    model = os.getenv("GEMINI_MODEL", os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"))
    console.print(
        f"[bold]Analyzing pending proposals[/bold] "
        f"[dim](backend: {backend_name}, model: {model})[/dim]\n"
    )

    engine = DecisionEngine()
    report = engine.process_all_pending(ticker=ticker, verbose=True)

    console.print(
        f"\n[bold]Done.[/bold] "
        f"Analyzed: {report.analyzed}/{report.total}  |  "
        f"Auto-decided: {report.auto_decided}  |  "
        f"[yellow]Needs review: {report.escalated}[/yellow]"
    )
    if report.errors:
        console.print(f"[red]Errors ({len(report.errors)}):[/red]")
        for e in report.errors:
            console.print(f"  • {e}")
    if report.escalated:
        console.print("\nRun [bold]review[/bold] to handle items needing your input.")


# ---------------------------------------------------------------------------
# review  (human-in-the-loop)
# ---------------------------------------------------------------------------

@click.command()
def review() -> None:
    """Interactive review queue — decide on proposals the AI flagged for you."""
    _ensure_db()

    from agent.decision_engine import DecisionEngine

    engine = DecisionEngine()
    queue = engine.get_review_queue()

    if not queue:
        console.print("[green]No proposals need your review right now.[/green]")
        return

    console.print(
        f"[bold]{len(queue)} proposal(s) need your input[/bold] "
        "(sorted by importance, highest first)\n"
    )

    for i, row in enumerate(queue, 1):
        concerns = json.loads(row["governance_concerns"] or "[]")
        conflicts = json.loads(row["conflicting_factors"] or "[]")

        panel_lines = [
            f"[bold]{row['ticker']}[/bold] — {row['company_name']}",
            f"Meeting: {row['meeting_date'] or 'unknown'}",
            f"Proposal #{row['proposal_number']}: [bold]{row['title']}[/bold]",
            f"Type: [dim]{row['proposal_type']}[/dim]",
            "",
            f"[bold]Proposal text:[/bold]",
            (row["full_text"] or "[no text]")[:600],
            "",
            f"[bold]AI recommendation:[/bold] " + (
                f"[{'green' if row['recommendation'] == 'FOR' else 'red' if row['recommendation'] == 'AGAINST' else 'yellow'}]"
                f"{row['recommendation']}[/]"
            ),
            f"Confidence: {row['confidence']:.0%}  Importance: {row['importance']:.0%}",
            "",
            f"[bold]Reasoning:[/bold] {row['reasoning']}",
        ]
        if concerns:
            panel_lines += ["", "[bold]Governance concerns:[/bold]"] + [f"  • {c}" for c in concerns]
        if conflicts:
            panel_lines += ["", "[bold]Why the AI is uncertain:[/bold]"] + [f"  • {c}" for c in conflicts]

        console.print(
            Panel(
                "\n".join(panel_lines),
                title=f"[bold]Item {i} of {len(queue)}[/bold]",
                border_style="cyan",
            )
        )

        vote = Prompt.ask(
            "Your vote",
            choices=["FOR", "AGAINST", "ABSTAIN", "SKIP"],
            default="SKIP",
        )
        if vote == "SKIP":
            console.print("[dim]Skipped.[/dim]\n")
            continue

        note = Prompt.ask("Add a note (optional)", default="")
        engine.apply_user_vote(
            decision_id=row["decision_id"],
            vote=vote,
            note=note,
            learn=True,
        )
        console.print(f"  [green]Recorded: {vote}[/green]\n")

    console.print("[bold]Review complete.[/bold] Run [bold]report[/bold] to see the full summary.")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@click.command()
@click.option("--ticker", "-t", default=None, help="Filter by ticker")
@click.option("--year", "-y", default=None, help="Filter by year (e.g. 2025)")
def report(ticker: str | None, year: str | None) -> None:
    """Print a voting report table for your portfolio."""
    _ensure_db()

    from data.storage import get_report_rows

    rows = get_report_rows(ticker=ticker, year=year)
    if not rows:
        console.print("[yellow]No proposals found for the given filters.[/yellow]")
        return

    tbl = Table(
        title="Governance Voting Report",
        show_lines=True,
        header_style="bold magenta",
    )
    tbl.add_column("Ticker", style="bold", width=8)
    tbl.add_column("Date", width=11)
    tbl.add_column("#", width=4)
    tbl.add_column("Proposal", max_width=40)
    tbl.add_column("Type", width=18)
    tbl.add_column("Mgmt", width=8)
    tbl.add_column("Vote", width=9)
    tbl.add_column("Conf", width=6)
    tbl.add_column("Source", width=8)

    for r in rows:
        final_vote = r["user_override"] or r["recommendation"]
        mgmt_rec = r["management_rec"] or "—"
        source = "User" if r["user_override"] else ("AI" if r["recommendation"] else "—")
        conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"

        vote_text = _vote_cell(final_vote)
        mgmt_text = _vote_cell(mgmt_rec if mgmt_rec != "—" else None)
        if mgmt_rec == "—":
            mgmt_text = Text("—", style="dim")

        flag = " [?]" if r["needs_review"] and not r["user_override"] else ""

        tbl.add_row(
            r["ticker"],
            r["meeting_date"] or "—",
            str(r["proposal_number"]),
            (r["title"] or "")[:40] + flag,
            (r["proposal_type"] or "").replace("_", " "),
            mgmt_text,
            vote_text,
            conf,
            source,
        )

    console.print(tbl)

    pending = sum(1 for r in rows if r["status"] == "pending")
    review_needed = sum(1 for r in rows if r["needs_review"] and not r["user_override"])
    if pending:
        console.print(f"\n[yellow]{pending} proposals still pending analysis.[/yellow] Run [bold]analyze[/bold].")
    if review_needed:
        console.print(f"[yellow]{review_needed} proposals need your review.[/yellow] Run [bold]review[/bold].")


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------

@click.group()
def preferences() -> None:
    """Manage your governance voting preferences."""


@preferences.command("list")
def prefs_list() -> None:
    """Show all current preferences (defaults + your overrides)."""
    _ensure_db()

    from agent.preference_engine import PreferenceEngine
    prefs = PreferenceEngine()
    console.print(prefs.get_context())

    overrides = prefs.list_overrides()
    if overrides:
        console.print(f"\n[bold]Your {len(overrides)} override(s) stored in DB:[/bold]")
        for o in overrides:
            console.print(f"  [cyan]{o['key']}[/cyan] = {o['value']}  [dim]({o['source']} · {o['created_at']})[/dim]")


@preferences.command("set")
@click.argument("key")
@click.argument("value")
def prefs_set(key: str, value: str) -> None:
    """Override a preference by dot-notation key."""
    _ensure_db()

    from agent.preference_engine import PreferenceEngine
    prefs = PreferenceEngine()
    prefs.update(key, value, source="user_statement")
    console.print(f"[green]Saved:[/green] {key} = {value}")


@preferences.command("learn")
def prefs_learn() -> None:
    """Enter a natural language statement to update your preferences."""
    _ensure_db()
    _check_llm_backend()

    from agent.analyzer import ProposalAnalyzer
    from agent.preference_engine import PreferenceEngine

    analyzer = ProposalAnalyzer()
    prefs = PreferenceEngine()

    console.print("[bold]Enter your preference statement[/bold] (or 'quit' to exit):")
    statement = Prompt.ask(">")
    if statement.lower() in ("quit", "q", ""):
        return

    with console.status("Extracting preferences …"):
        extracted = prefs.learn_from_statement(statement, analyzer)

    if not extracted:
        console.print("[yellow]Could not extract specific preferences. Try being more specific.[/yellow]")
    else:
        console.print(f"[green]Learned {len(extracted)} preference(s):[/green]")
        for item in extracted:
            console.print(f"  [cyan]{item['key']}[/cyan] = {item['value']}")
            console.print(f"    [dim]{item.get('description', '')}[/dim]")


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

@click.command()
def chat() -> None:
    """Conversational interface — ask governance questions or discuss your portfolio."""
    _ensure_db()
    _check_llm_backend()

    from agent.analyzer import ProposalAnalyzer
    from agent.preference_engine import PreferenceEngine
    from data.storage import add_conversation_message, get_conversation_history

    analyzer = ProposalAnalyzer()
    prefs = PreferenceEngine()

    backend_name = os.getenv("LLM_BACKEND", "anthropic")
    console.print(
        Panel(
            f"Ask anything about corporate governance, your portfolio companies, "
            f"or specific proposals.\n"
            f"Backend: [bold]{backend_name}[/bold]\n"
            f"Type [bold]exit[/bold] or [bold]quit[/bold] to leave.",
            title="Governance Agent Chat",
            border_style="blue",
        )
    )

    history_rows = get_conversation_history(limit=20)
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]

    while True:
        user_msg = Prompt.ask("[bold blue]You[/bold blue]")
        if user_msg.lower() in ("exit", "quit", "q"):
            break

        add_conversation_message("user", user_msg)

        with console.status("Thinking …"):
            reply = analyzer.chat(
                user_message=user_msg,
                history=history,
                preferences_context=prefs.get_context(),
            )

        add_conversation_message("assistant", reply)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": reply})

        console.print(Panel(reply, title="[bold green]Agent[/bold green]", border_style="green"))


# ---------------------------------------------------------------------------
# digest  (one-shot pipeline: fetch → analyze → email)
# ---------------------------------------------------------------------------

@click.command()
def digest() -> None:
    """Run the full pipeline once: fetch → analyze → send email digest."""
    _ensure_db()
    _check_llm_backend()

    from scheduler import run_pipeline
    run_pipeline(verbose=True)


# ---------------------------------------------------------------------------
# schedule  (long-running daemon)
# ---------------------------------------------------------------------------

@click.command("schedule")
def schedule_daemon() -> None:
    """Run as a daemon — triggers the pipeline every Monday morning."""
    _ensure_db()

    from scheduler import run_daemon, print_cron_instructions

    console.print(
        Panel(
            "The scheduler will run the full pipeline (fetch → analyze → email) "
            "automatically. Keep this process running in the background.\n\n"
            "Alternative: use a cron job instead (see below).",
            title="Governance Agent Scheduler",
            border_style="blue",
        )
    )
    print_cron_instructions()
    console.print("")

    run_daemon(verbose=True)


# ---------------------------------------------------------------------------
# setup-gemini
# ---------------------------------------------------------------------------

@click.command("meeting-check")
@click.option("--dry-run", is_flag=True, help="Show what would fire without calling LLMs or notifiers")
@click.option("--tier", default=None, help="Restrict to one tier (e.g. T-7)")
def meeting_check(dry_run: bool, tier: str | None) -> None:
    """Fan out tier-based alerts (T-14/T-7/T-3/T-1) for upcoming meetings."""
    _ensure_db()
    if not dry_run:
        _check_llm_backend()

    from scheduler import run_meeting_check
    summary = run_meeting_check(verbose=True, dry_run=dry_run, only_tier=tier)
    if summary["errors"]:
        console.print(f"[red]{len(summary['errors'])} error(s) during meeting-check.[/red]")


@click.command("telegram-bot")
def telegram_bot_cmd() -> None:
    """Run the Telegram bot worker (long-polling). Blocks forever."""
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        console.print(
            "[bold red]TELEGRAM_BOT_TOKEN not set.[/bold red]\n"
            "1. Create a bot via @BotFather and copy the token\n"
            "2. Add to .env:\n"
            "     TELEGRAM_BOT_TOKEN=...\n"
            "     TELEGRAM_CHAT_ID=...\n"
            "3. Send any message to your bot, then visit\n"
            "   https://api.telegram.org/bot<TOKEN>/getUpdates\n"
            "   and copy chat.id."
        )
        sys.exit(1)
    _ensure_db()

    from telegram_bot import run as run_bot
    run_bot()


@click.command("backfill-meeting-dates")
@click.option("--ticker", "-t", default=None, help="Backfill only this ticker")
def backfill_meeting_dates(ticker: str | None) -> None:
    """Re-parse cached EDGAR proxies to populate meeting_date / vote_deadline / meeting_url."""
    _ensure_db()

    from data.edgar import EDGARClient, extract_meeting_metadata
    from data.storage import update_proposal_meeting_metadata, get_all_proposals

    client = EDGARClient()
    rows = get_all_proposals(ticker=ticker)
    if not rows:
        console.print("[yellow]No proposals to backfill.[/yellow]")
        return

    # Group by accession_number so we only re-download each filing once
    by_filing: dict[tuple[str, str], list] = {}
    for r in rows:
        if not r["accession_number"]:
            continue
        by_filing.setdefault((r["ticker"], r["accession_number"]), []).append(r)

    from config import PORTFOLIO
    updated = 0
    for (t, accession), proposal_rows in by_filing.items():
        # Reconstruct the filing index URL
        company = PORTFOLIO.get(t, {})
        cik = company.get("cik")
        if not cik:
            continue
        cik_int = int(cik)
        accession_clean = accession.replace("-", "")

        # Look at every cached HTML doc for this filing
        from pathlib import Path
        from config import EDGAR_CACHE_DIR
        cache_dir = Path(EDGAR_CACHE_DIR)
        if not cache_dir.exists():
            continue

        # We don't know the URL hash, so iterate cache files referenced by this filing.
        # Heuristic: find filing index, get the doc URL, then read its cache.
        try:
            from data.edgar import Filing
            filing = Filing(
                accession_number=accession,
                filing_date=proposal_rows[0]["filing_date"] or "",
                form_type="DEF 14A",
                primary_document="",
                cik=str(cik_int),
            )
            url = client.get_filing_document_url(filing)
            if not url:
                continue
            doc = client.download_document(url)
            md = extract_meeting_metadata(doc.text)
            if not md.meeting_date:
                continue
            for r in proposal_rows:
                update_proposal_meeting_metadata(
                    proposal_id=r["id"],
                    meeting_date=md.meeting_date,
                    vote_deadline=md.vote_deadline,
                    meeting_url=md.meeting_url,
                )
                updated += 1
            console.print(
                f"  [green]{t}[/green] {accession} → meeting {md.meeting_date}"
                f"{', deadline ' + md.vote_deadline if md.vote_deadline else ''}"
                f" ({len(proposal_rows)} proposals updated)"
            )
        except Exception as exc:
            console.print(f"  [red]{t} {accession}: {exc}[/red]")

    console.print(f"\n[bold]Backfill complete:[/bold] {updated} proposal row(s) updated.")


@click.command("seed-test-meeting")
@click.option("--ticker", "-t", required=True, help="Ticker (must be in your portfolio)")
@click.option("--days", "-d", default=7, show_default=True, help="Days from today")
@click.option("--proposal-number", default="999", show_default=True)
def seed_test_meeting(ticker: str, days: int, proposal_number: str) -> None:
    """Insert a synthetic proposal whose meeting is N days away (for testing alerts)."""
    _ensure_db()

    from datetime import datetime, timedelta
    from config import PORTFOLIO
    from data.storage import upsert_proposal

    ticker = ticker.upper()
    if ticker not in PORTFOLIO:
        console.print(f"[red]{ticker} is not in your portfolio.[/red]")
        sys.exit(1)

    meeting_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    proposal_id = upsert_proposal(
        ticker=ticker,
        company_name=PORTFOLIO[ticker]["name"],
        proposal_number=proposal_number,
        title=f"[TEST] Synthetic proposal {days} days from today",
        full_text="Test proposal seeded by seed-test-meeting; safe to delete.",
        management_rec="FOR",
        proposal_type="other",
        source="manual",
        meeting_date=meeting_date,
        accession_number=f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    console.print(
        f"[green]Seeded[/green] test proposal id={proposal_id} for {ticker}, "
        f"meeting on {meeting_date} (T-{days})"
    )


@click.command("record-outcome")
@click.option("--proposal-id", required=True, type=int, help="proposals.id")
@click.option("--outcome", required=True, type=click.Choice(["FOR", "AGAINST", "ABSTAIN", "WITHHOLD"]))
@click.option("--support-pct", default=None, type=float, help="Shareholder support percentage (0-100)")
@click.option("--source", default="manual")
def record_outcome(proposal_id: int, outcome: str, support_pct: float | None, source: str) -> None:
    """Record the actual shareholder vote outcome for accuracy tracking."""
    _ensure_db()

    from data.storage import record_vote_outcome, get_accuracy_summary
    record_vote_outcome(proposal_id, outcome, support_pct, source)

    summary = get_accuracy_summary()
    if summary["total_with_outcome"]:
        console.print(
            f"[green]Recorded.[/green] AI agreement with shareholder outcomes: "
            f"{summary['agree_with_outcome']}/{summary['total_with_outcome']} "
            f"({summary['agreement_pct']:.0%})"
        )
    else:
        console.print("[green]Recorded.[/green]")


@click.command("setup-local")
@click.option("--url", default=None, help="LOCAL_LLM_URL (e.g. http://127.0.0.1:1234/v1)")
@click.option("--model", default=None, help="LOCAL_LLM_MODEL")
def setup_local(url: str | None, model: str | None) -> None:
    """Verify the local-LLM backend (gbrain / LM Studio / llama-server / …) is reachable."""
    url = url or os.getenv("LOCAL_LLM_URL", "")
    model = model or os.getenv("LOCAL_LLM_MODEL", "")

    if not url:
        console.print(
            "[bold red]LOCAL_LLM_URL not set.[/bold red]\n"
            "Add to .env, e.g.:\n"
            "  LLM_BACKEND=local\n"
            "  LOCAL_LLM_URL=http://127.0.0.1:1234/v1\n"
            "  LOCAL_LLM_MODEL=your-local-model-name\n"
        )
        return

    console.print(
        f"Testing local LLM at [bold]{url}[/bold] with model [bold]{model or 'default'}[/bold] …"
    )
    from agent.llm_backend import LocalLLMBackend
    backend = LocalLLMBackend(url=url, model=model)
    if backend.test_connection():
        console.print("[green]OK[/green] — local LLM is reachable and responding.")
    else:
        console.print(
            "[red]Failed.[/red] Check that the server is running and the URL/model are correct."
        )


@click.command("setup-gemini")
@click.option("--model", "-m", default=None, help="Gemini model (default: gemini-2.5-flash-lite)")
def setup_gemini(model: str | None) -> None:
    """Verify Gemini API key and test the model."""
    model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    api_key = os.getenv("GEMINI_API_KEY", "")

    if not api_key:
        console.print(
            "[bold red]GEMINI_API_KEY not set.[/bold red]\n\n"
            "1. Go to [bold]https://aistudio.google.com/apikey[/bold]\n"
            "2. Create an API key\n"
            "3. Add to your .env file:\n"
            "   GEMINI_API_KEY=your-key-here\n"
        )
        return

    console.print(f"Testing Gemini API with model [bold]{model}[/bold] …")

    try:
        from agent.llm_backend import GeminiBackend
        backend = GeminiBackend(model=model, api_key=api_key)
    except Exception as exc:
        console.print(f"  [red]Failed to initialize: {exc}[/red]")
        return

    # List available models
    console.print("  Checking available models …")
    models = backend.list_models()
    if models:
        gemini_models = [m for m in models if "gemini" in m.lower()]
        console.print(
            f"  [green]API key valid.[/green] "
            f"{len(gemini_models)} Gemini model(s) available."
        )
    else:
        console.print("  [yellow]Could not list models (key may still work).[/yellow]")

    # Quick test
    console.print(f"  Running test with {model} …")
    if backend.test_connection():
        console.print(f"  [green]Test passed.[/green] Model '{model}' is working.")
    else:
        console.print(f"  [red]Test failed.[/red] Check your API key and model name.")
        return

    console.print(
        f"\n[bold]To use Gemini, set in your .env:[/bold]\n"
        f"  LLM_BACKEND=gemini\n"
        f"  GEMINI_API_KEY={api_key[:8]}…\n"
        f"  GEMINI_MODEL={model}\n"
    )


@click.command("setup-ollama")
@click.option("--url", default=None, help="Ollama URL (default: http://127.0.0.1:11434)")
@click.option("--model", "-m", default=None, help="Model to test (default: OLLAMA_MODEL env or llama3.1:8b)")
def setup_ollama(url: str | None, model: str | None) -> None:
    """Verify Ollama is running and list available models."""
    base_url = url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    test_model = model or os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    console.print(f"Checking Ollama at [bold]{base_url}[/bold] …")

    import requests as _req
    try:
        r = _req.get(f"{base_url}/api/tags", timeout=5)
        r.raise_for_status()
        tags = r.json().get("models", [])
    except Exception as exc:
        console.print(
            f"[bold red]Cannot reach Ollama.[/bold red] {exc}\n\n"
            "Make sure Ollama is installed and running:\n"
            "  ollama serve\n"
            "Then pull a model:\n"
            "  ollama pull llama3.1:8b\n"
        )
        return

    if tags:
        console.print(f"[green]Ollama is running.[/green] {len(tags)} model(s) installed:")
        for m in tags:
            name = m.get("name", m.get("model", "?"))
            size_gb = m.get("size", 0) / 1e9
            console.print(f"  • {name}  ({size_gb:.1f} GB)")
    else:
        console.print(
            "[yellow]Ollama is running but no models are installed.[/yellow]\n"
            "Pull a model first:\n"
            "  ollama pull llama3.1:8b\n"
        )
        return

    console.print(f"\nRunning inference test with [bold]{test_model}[/bold] …")
    from agent.llm_backend import OllamaBackend
    backend = OllamaBackend(url=base_url, model=test_model)
    if backend.test_connection():
        console.print(f"[green]Test passed.[/green] '{test_model}' is responding.")
    else:
        console.print(
            f"[red]Test failed.[/red] Model '{test_model}' didn't respond.\n"
            f"If the model name is different, pass it with --model or set OLLAMA_MODEL in .env."
        )
        return

    console.print(
        f"\n[bold]To use Ollama, set in your .env:[/bold]\n"
        f"  LLM_BACKEND=ollama\n"
        f"  OLLAMA_URL={base_url}\n"
        f"  OLLAMA_MODEL={test_model}\n"
    )
