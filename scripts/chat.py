#!/usr/bin/env python3
"""
OpenClaw skill script: ask a freeform governance question.

Usage:
  python3 scripts/chat.py "What is TSLA's board composition like?"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db, get_conversation_history, add_conversation_message
from agent.analyzer import ProposalAnalyzer
from agent.preference_engine import PreferenceEngine


def main():
    if len(sys.argv) < 2:
        print("Usage: chat.py \"your question here\"")
        return

    question = " ".join(sys.argv[1:])

    init_db()

    analyzer = ProposalAnalyzer()
    prefs = PreferenceEngine()

    # Load recent conversation history for context
    history_rows = get_conversation_history(limit=10)
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]

    add_conversation_message("user", question)

    reply = analyzer.chat(
        user_message=question,
        history=history,
        preferences_context=prefs.get_context(),
    )

    add_conversation_message("assistant", reply)

    print(reply)


if __name__ == "__main__":
    main()
