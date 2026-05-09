"""Notifier protocol — pluggable alert + digest channels.

Implementations:
  - notifiers/email.py    SMTP HTML digest (existing behavior, wrapped)
  - notifiers/telegram.py Telegram Bot API with inline-button vote callbacks

The scheduler dispatches through `get_active_notifiers()` from the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class AlertTier:
    """Pre-meeting alert tier: how many days out, and a short label.

    Tiers fire once per (proposal, tier, channel) — see meeting_alerts table.
    Default tiers: T-14, T-7, T-3, T-1.
    """
    days_out: int
    label: str

    @property
    def code(self) -> str:
        return f"T-{self.days_out}"


@runtime_checkable
class Notifier(Protocol):
    """Plug-in interface for any channel that can deliver governance alerts.

    Implementations must be importable even when the channel is misconfigured;
    `is_configured()` lets the registry filter inactive channels at load time
    without raising.
    """
    name: str

    def is_configured(self) -> bool:
        """True if all required env vars / credentials are present."""
        ...

    def send_alert(
        self,
        proposal: dict,
        decision: dict,
        tier: AlertTier,
    ) -> str:
        """Send a pre-meeting alert for one proposal.

        Returns a channel-specific message id (Telegram message id, SMTP
        Message-ID header, etc.) — used for editing/replying later. Empty
        string if the channel doesn't track ids.
        """
        ...

    def send_digest(self, rows: list[dict], week_label: str) -> bool:
        """Send the weekly digest of upcoming votes. Returns True on success."""
        ...
