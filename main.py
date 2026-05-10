"""
Government Agent — Personal Shareholder Voting Assistant

Usage:
  python main.py --help
  python main.py fetch [--ticker TSLA] [--months 18]
  python main.py add [--ticker TTE]
  python main.py analyze [--ticker NVDA]
  python main.py review
  python main.py report [--ticker GOOGL] [--year 2025]
  python main.py preferences list
  python main.py preferences set KEY VALUE
  python main.py preferences learn
  python main.py chat
  python main.py digest                       # one-shot weekly: fetch → analyze → notify
  python main.py meeting-check                # one-shot daily fan-out (T-14/T-7/T-3/T-1)
  python main.py schedule                     # daemon: weekly digest + daily meeting-check
  python main.py telegram-bot                 # long-polling worker for inline-button callbacks
  python main.py backfill-meeting-dates       # populate meeting_date on existing rows
  python main.py seed-test-meeting -t OXY -d 7  # synthetic test meeting
  python main.py record-outcome --proposal-id 5 --outcome FOR
  python main.py setup-local                  # verify local LLM (gbrain / LM Studio / …)
  python main.py setup-ollama                 # verify Ollama is running and list models
  python main.py setup-gemini                 # verify Gemini API
"""

import click

from ui.cli import (
    fetch,
    add_proposal,
    analyze,
    review,
    report,
    preferences,
    chat,
    digest,
    schedule_daemon,
    setup_gemini,
    setup_local,
    setup_ollama,
    meeting_check,
    telegram_bot_cmd,
    backfill_meeting_dates,
    seed_test_meeting,
    record_outcome,
)


@click.group()
@click.version_option("3.0.0", prog_name="government-agent")
def cli() -> None:
    """Personal shareholder governance agent.

    Monitors proxy votes for your portfolio companies, makes AI-powered
    voting recommendations with two-pass critique, fans out tier-based
    alerts (T-14/T-7/T-3/T-1) before each shareholder meeting via Telegram
    or email, and records your votes via inline buttons.

    Backends: anthropic | gemini | local (gbrain / LM Studio / …) | ollama

    Portfolio: SYF OXY TTE WISE BABA BIDU TSLA GOOGL NVDA
               DOYU AAL USB STZ POOL LEN 1810.HK UNH
    """


cli.add_command(fetch)
cli.add_command(add_proposal, name="add")
cli.add_command(analyze)
cli.add_command(review)
cli.add_command(report)
cli.add_command(preferences)
cli.add_command(chat)
cli.add_command(digest)
cli.add_command(schedule_daemon, name="schedule")
cli.add_command(meeting_check, name="meeting-check")
cli.add_command(telegram_bot_cmd, name="telegram-bot")
cli.add_command(backfill_meeting_dates, name="backfill-meeting-dates")
cli.add_command(seed_test_meeting, name="seed-test-meeting")
cli.add_command(record_outcome, name="record-outcome")
cli.add_command(setup_gemini, name="setup-gemini")
cli.add_command(setup_local, name="setup-local")
cli.add_command(setup_ollama, name="setup-ollama")


if __name__ == "__main__":
    cli()
