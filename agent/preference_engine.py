"""
User preference management.

Preferences are stored in two layers (merged at runtime):
  1. preferences/default_preferences.json  — baseline governance principles
  2. SQLite `preferences` table            — user overrides and learned preferences

The merged result is passed as context to every Claude analysis call.
"""

import json
import os
from pathlib import Path

from data import storage

DEFAULT_PREFS_PATH = Path(__file__).parent.parent / "preferences" / "default_preferences.json"


class PreferenceEngine:

    def load(self) -> dict:
        """Return merged preferences: defaults overridden by DB entries."""
        # 1. Load defaults from JSON
        try:
            with open(DEFAULT_PREFS_PATH) as f:
                prefs = json.load(f)
        except FileNotFoundError:
            prefs = {}

        # 2. Apply DB overrides (dot-notation keys override nested JSON)
        for row in storage.get_latest_preferences():
            key = row["key"]
            raw_value = row["value"]
            # Try to parse value as JSON (handles booleans, numbers, strings)
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, ValueError):
                value = raw_value

            # Navigate to the right nested dict and set the value
            parts = key.split(".")
            target = prefs
            for part in parts[:-1]:
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value

        return prefs

    def update(self, key: str, value: str, source: str = "user_statement") -> None:
        """Store a preference override in the DB."""
        storage.save_preference(key, value, source)

    def get_context(self) -> str:
        """Return a human-readable summary of current preferences for Claude prompts."""
        prefs = self.load()
        lines = ["## User Governance Preferences\n"]

        def _render(d: dict, indent: int = 0) -> None:
            prefix = "  " * indent
            for k, v in d.items():
                if k.startswith("_") or k.startswith("comment"):
                    continue
                if isinstance(v, dict):
                    lines.append(f"{prefix}**{k}:**")
                    _render(v, indent + 1)
                else:
                    lines.append(f"{prefix}- {k}: {v}")

        _render(prefs)
        return "\n".join(lines)

    def learn_from_statement(self, statement: str, analyzer) -> list[dict]:
        """
        Extract preference updates from a natural language statement.
        Persists extracted preferences and returns them.

        analyzer: agent.analyzer.ProposalAnalyzer
        """
        extracted = analyzer.extract_preferences_from_statement(statement)
        for item in extracted:
            key = item.get("key", "")
            value = item.get("value")
            if key and value is not None:
                self.update(key, json.dumps(value), source="user_statement")
        return extracted

    def learn_from_vote(
        self,
        ticker: str,
        proposal_title: str,
        proposal_type: str,
        user_vote: str,
        ai_recommendation: str,
        analyzer,
    ) -> None:
        """
        When a user overrides the AI recommendation, try to learn a preference.
        Only records when the user explicitly voted against the AI's recommendation.
        """
        if user_vote == ai_recommendation:
            return  # No disagreement — nothing to learn

        note = (
            f"User voted {user_vote} (AI said {ai_recommendation}) on "
            f"{ticker} proposal '{proposal_title}' (type: {proposal_type})"
        )
        statement = (
            f"For {proposal_type} proposals like '{proposal_title}' at companies "
            f"like {ticker}, I prefer to vote {user_vote}."
        )
        extracted = self.learn_from_statement(statement, analyzer)
        if not extracted:
            # Store a raw note even if no structured preference could be extracted
            self.update(
                key=f"learned.{ticker}.{proposal_type}",
                value=json.dumps(
                    {"vote": user_vote, "proposal": proposal_title}
                ),
                source="learned_vote",
            )

    def list_overrides(self) -> list[dict]:
        """Return all DB-stored preference overrides as plain dicts."""
        rows = storage.get_all_preferences()
        return [
            {
                "key": r["key"],
                "value": r["value"],
                "source": r["source"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
