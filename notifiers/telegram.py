"""Telegram notifier — sends actionable alerts with FOR/AGAINST/ABSTAIN
inline buttons. Callbacks are handled separately in telegram_bot.py.

Implementation note: this module uses the Telegram Bot HTTP API directly
via `requests` rather than python-telegram-bot, so the alert path stays
synchronous and the Mac mini doesn't need an asyncio event loop running
just to send a message. The interactive bot worker (telegram_bot.py)
uses python-telegram-bot for long-polling and callback dispatch.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from notifiers.base import AlertTier


TG_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    # ------------------------------------------------------------------

    def send_alert(self, proposal: dict, decision: dict, tier: AlertTier) -> str:
        """Post an inline-keyboard alert. Returns the Telegram message_id."""
        if not self.is_configured():
            return ""

        text = self._build_alert_text(proposal, decision, tier)
        decision_id = decision.get("decision_id") or decision.get("id") or 0
        keyboard = self._build_vote_keyboard(decision_id)

        try:
            resp = requests.post(
                f"{TG_API_BASE}/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                    "reply_markup": keyboard,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return str(data["result"].get("message_id", ""))
            print(f"Telegram alert failed: {data}")
            return ""
        except Exception as exc:
            print(f"Telegram alert exception: {exc}")
            return ""

    def send_digest(self, rows: list[dict], week_label: str) -> bool:
        """Post a compact weekly digest. Long output is chunked under
        Telegram's 4096-char message limit."""
        if not self.is_configured():
            return False

        chunks = self._build_digest_chunks(rows, week_label)
        ok = True
        for chunk in chunks:
            try:
                resp = requests.post(
                    f"{TG_API_BASE}/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                ok = ok and resp.json().get("ok", False)
            except Exception as exc:
                print(f"Telegram digest exception: {exc}")
                ok = False
        return ok

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_vote_keyboard(decision_id: int) -> dict[str, Any]:
        # Callback data must stay under 64 bytes per button. `vote:<id>:<choice>`
        # easily fits even for 7-digit ids.
        return {
            "inline_keyboard": [[
                {"text": "FOR", "callback_data": f"vote:{decision_id}:FOR"},
                {"text": "AGAINST", "callback_data": f"vote:{decision_id}:AGAINST"},
                {"text": "ABSTAIN", "callback_data": f"vote:{decision_id}:ABSTAIN"},
                {"text": "Details", "callback_data": f"details:{decision_id}"},
            ]]
        }

    @staticmethod
    def _build_alert_text(proposal: dict, decision: dict, tier: AlertTier) -> str:
        ticker = proposal.get("ticker", "?")
        company = proposal.get("company_name", "")
        meeting = proposal.get("meeting_date") or "unknown"
        prop_no = proposal.get("proposal_number", "?")
        title = (proposal.get("title", "") or "")[:120]
        mgmt = proposal.get("management_rec") or "—"
        rec = decision.get("recommendation") or "—"
        conf = decision.get("confidence") or 0.0
        imp = decision.get("importance") or 0.0
        pass1 = decision.get("pass1_recommendation") or rec
        revised = decision.get("critique_revised")
        reasoning = (decision.get("reasoning") or "")[:280]

        revision_line = ""
        if pass1 and pass1 != rec:
            revision_line = f"\nPass1→{pass1} | Pass2→{rec} (REVISED BY CRITIQUE)"
        elif revised:
            revision_line = f"\nPass1→{pass1} | Pass2→{rec}"

        return (
            f"*[{ticker}]* {company} — Meeting {meeting} (*{tier.code}*)\n"
            f"#{prop_no} {title}\n"
            f"Mgmt: {mgmt} | AI: *{rec}* (conf {conf:.0%}, importance {imp:.0%})"
            f"{revision_line}\n"
            f"_Why:_ {reasoning}"
        )

    @staticmethod
    def _build_digest_chunks(rows: list[dict], week_label: str) -> list[str]:
        header = f"*Governance Digest — Week of {week_label}*\n"
        if not rows:
            return [header + "_No upcoming votes._"]

        lines: list[str] = [header]
        review_count = sum(1 for r in rows if r.get("needs_review") and not r.get("user_override"))
        lines.append(
            f"{len(rows)} proposals · {review_count} need your review\n"
        )
        for r in rows:
            final = r.get("user_override") or r.get("recommendation") or "—"
            ticker = r.get("ticker", "?")
            meeting = r.get("meeting_date") or "?"
            prop_no = r.get("proposal_number", "?")
            title = (r.get("title", "") or "")[:60]
            flag = " ❗" if r.get("needs_review") and not r.get("user_override") else ""
            lines.append(f"`{ticker}` {meeting} #{prop_no} → *{final}*{flag} {title}")

        # Chunk into <=4000-char pieces (under Telegram's 4096 limit, leaving margin)
        chunks: list[str] = []
        buf = ""
        for line in lines:
            if len(buf) + len(line) + 1 > 4000 and buf:
                chunks.append(buf)
                buf = ""
            buf += line + "\n"
        if buf:
            chunks.append(buf)
        return chunks
