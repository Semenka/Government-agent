"""
Government Agent — Personal Shareholder Voting Assistant

Maximizes shareholder value by analyzing corporate votes and sending
weekly recommendations before US market open via email and WhatsApp.

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
  python main.py digest [--channel whatsapp]  # One-shot: fetch → analyze → send digest
  python main.py schedule                     # Daemon: Monday pre-market digest
  python main.py setup-gemini [--model X]      # Verify Gemini API key + test
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
)


@click.group()
@click.version_option("2.0.0", prog_name="government-agent")
def cli() -> None:
    """Personal shareholder governance agent.

    Monitors proxy votes for your portfolio companies, makes AI-powered
    voting recommendations, and escalates important decisions to you.

    Supports both Anthropic (Claude) and Gemini (2.5 Flash-Lite) backends.

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
cli.add_command(setup_gemini, name="setup-gemini")


if __name__ == "__main__":
    cli()
