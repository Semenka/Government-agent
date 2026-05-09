"""Notifiers package — pluggable alert channels for the governance agent.

Currently ships email (SMTP) and Telegram. Add new channels by implementing
the Notifier protocol in notifiers/base.py and registering them in
notifiers/registry.py.
"""

from notifiers.base import Notifier, AlertTier
from notifiers.registry import get_active_notifiers

__all__ = ["Notifier", "AlertTier", "get_active_notifiers"]
