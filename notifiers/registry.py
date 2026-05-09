"""Notifier registry — picks active channels based on env config.

Set NOTIFIER_CHANNELS in .env, comma-separated:
  NOTIFIER_CHANNELS=telegram,email
  NOTIFIER_CHANNELS=telegram        # Telegram only
  NOTIFIER_CHANNELS=email           # email only

Channels listed but unconfigured (missing token / SMTP creds) are silently
skipped — handy for development.
"""

from __future__ import annotations

import os

from notifiers.base import Notifier
from notifiers.email import EmailNotifier
from notifiers.telegram import TelegramNotifier


_BUILDERS = {
    "email": EmailNotifier,
    "telegram": TelegramNotifier,
}


def _channels_from_env() -> list[str]:
    raw = os.getenv("NOTIFIER_CHANNELS", "").strip()
    if not raw:
        # Default: prefer Telegram if configured, otherwise email
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            return ["telegram"]
        return ["email"]
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def get_active_notifiers() -> list[Notifier]:
    """Instantiate and return the configured-and-ready channel notifiers."""
    active: list[Notifier] = []
    for channel in _channels_from_env():
        builder = _BUILDERS.get(channel)
        if not builder:
            continue
        instance = builder()
        if instance.is_configured():
            active.append(instance)
    return active
