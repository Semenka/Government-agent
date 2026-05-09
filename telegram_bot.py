"""Long-polling Telegram bot worker.

Run as a sibling process to the scheduler daemon:

    python main.py schedule       # daemon: weekly digest + daily meeting-check
    python main.py telegram-bot   # this worker: handles inline-button callbacks

Both share the SQLite database via WAL (already enabled in data/storage.py).

Why long-polling, not webhook: the Mac mini lives behind NAT. Webhooks need
public TLS; long-polling does not. Telegram tolerates one process per bot
token holding a long-poll connection.

Implements:
  /start, /help, /queue, /upcoming  — slash commands
  vote:<decision_id>:<choice>       — inline-button callbacks
  details:<decision_id>             — show expanded reasoning
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# python-telegram-bot is optional at install time; we import lazily so the
# rest of the package doesn't require it.

from data.storage import (
    init_db,
    get_decision_with_proposal,
    list_upcoming_meetings,
    get_review_queue,
)
from agent.decision_engine import DecisionEngine

log = logging.getLogger("telegram_bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update, context):
    await update.message.reply_text(
        "Governance Agent online. Commands:\n"
        "/queue — proposals needing your review\n"
        "/upcoming — meetings in the next 30 days\n"
        "/help — show this message"
    )


async def cmd_help(update, context):
    await cmd_start(update, context)


async def cmd_queue(update, context):
    rows = get_review_queue()
    if not rows:
        await update.message.reply_text("No proposals in the review queue.")
        return

    for r in rows[:10]:
        text = (
            f"*[{r['ticker']}]* #{r['proposal_number']} {r['title'][:80]}\n"
            f"Meeting: {r['meeting_date'] or 'unknown'}\n"
            f"AI: *{r['recommendation']}* "
            f"(conf {r['confidence']:.0%}, importance {r['importance']:.0%})\n"
            f"_Why:_ {(r['reasoning'] or '')[:240]}"
        )
        keyboard = _vote_keyboard(r["decision_id"])
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    if len(rows) > 10:
        await update.message.reply_text(
            f"Showing first 10 of {len(rows)} review items."
        )


async def cmd_upcoming(update, context):
    rows = list_upcoming_meetings(days_ahead=30)
    if not rows:
        await update.message.reply_text("No meetings scheduled in the next 30 days.")
        return

    today = datetime.now().date()
    lines = ["*Upcoming meetings (30 days):*"]
    for r in rows:
        try:
            meeting = datetime.strptime(r["meeting_date"], "%Y-%m-%d").date()
            days = (meeting - today).days
            tier = _tier_for_days(days)
        except ValueError:
            days = "?"
            tier = ""
        lines.append(
            f"`{r['ticker']}` {r['meeting_date']} "
            f"(T-{days}{', ' + tier if tier else ''}) — "
            f"{r['proposal_count']} proposals, {r['voted_count']} voted"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def callback_vote(update, context):
    """Handle inline-button presses on alerts."""
    query = update.callback_query
    data = query.data or ""

    # Always ack within 3s — Telegram requirement
    await query.answer()

    if data.startswith("vote:"):
        try:
            _, decision_id_str, choice = data.split(":", 2)
            decision_id = int(decision_id_str)
        except ValueError:
            await query.edit_message_text("Malformed callback data.")
            return

        choice = choice.upper()
        if choice not in {"FOR", "AGAINST", "ABSTAIN", "WITHHOLD"}:
            await query.edit_message_text(f"Unknown vote choice: {choice}")
            return

        engine = DecisionEngine()
        ok = engine.apply_user_vote(
            decision_id=decision_id, vote=choice, note="(via Telegram)", learn=True,
        )
        if not ok:
            await query.edit_message_text(
                f"Decision {decision_id} not found — may already be voted."
            )
            return

        original = query.message.text or query.message.caption or ""
        await query.edit_message_text(
            f"{original}\n\n✅ Recorded vote: *{choice}*",
            parse_mode="Markdown",
        )
        return

    if data.startswith("details:"):
        try:
            decision_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.edit_message_text("Malformed callback data.")
            return

        row = get_decision_with_proposal(decision_id)
        if row is None:
            await query.message.reply_text("Decision not found.")
            return

        concerns = json.loads(row["governance_concerns"] or "[]")
        conflicts = json.loads(row["conflicting_factors"] or "[]")
        aligned = json.loads(row["aligned_preferences"] or "[]")

        details = [
            f"*[{row['ticker']}]* #{row['proposal_number']} — {row['title']}",
            f"Meeting: {row['meeting_date'] or 'unknown'}",
            "",
            f"*Reasoning:* {row['reasoning'] or '(none)'}",
        ]
        if concerns:
            details += ["", "*Concerns:*"] + [f"• {c}" for c in concerns]
        if conflicts:
            details += ["", "*Why uncertain:*"] + [f"• {c}" for c in conflicts]
        if aligned:
            details += ["", "*Aligned preferences:*"] + [f"• {c}" for c in aligned]

        await query.message.reply_text(
            "\n".join(details), parse_mode="Markdown",
        )
        return


def _vote_keyboard(decision_id: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("FOR", callback_data=f"vote:{decision_id}:FOR"),
        InlineKeyboardButton("AGAINST", callback_data=f"vote:{decision_id}:AGAINST"),
        InlineKeyboardButton("ABSTAIN", callback_data=f"vote:{decision_id}:ABSTAIN"),
        InlineKeyboardButton("Details", callback_data=f"details:{decision_id}"),
    ]])


def _tier_for_days(days: int) -> str:
    if days <= 1:
        return "T-1 urgent"
    if days <= 3:
        return "T-3"
    if days <= 7:
        return "T-7"
    if days <= 14:
        return "T-14"
    return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the Telegram long-polling worker. Blocks forever."""
    init_db()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not set. Create a bot via @BotFather, then "
            "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env."
        )

    try:
        from telegram.ext import (
            Application,
            CommandHandler,
            CallbackQueryHandler,
        )
    except ImportError as exc:
        raise RuntimeError(
            "python-telegram-bot is not installed. Install it with:\n"
            "  pip install -r requirements.txt"
        ) from exc

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CallbackQueryHandler(callback_vote))

    log.info("Telegram bot starting (long-polling)…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    run()
