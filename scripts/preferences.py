#!/usr/bin/env python3
"""
OpenClaw skill script: show or update governance voting preferences.

Usage:
  python3 scripts/preferences.py list
  python3 scripts/preferences.py set executive_comp.max_acceptable_ratio 200
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.storage import init_db
from agent.preference_engine import PreferenceEngine


def main():
    if len(sys.argv) < 2:
        print("Usage: preferences.py [list|set KEY VALUE]")
        return

    init_db()
    prefs = PreferenceEngine()

    command = sys.argv[1]

    if command == "list":
        context = prefs.get_context()
        print("*Current Voting Preferences*")
        print()
        print(context)

        overrides = prefs.list_overrides()
        if overrides:
            print(f"\n*Your {len(overrides)} override(s):*")
            for o in overrides:
                print(f"  {o['key']} = {o['value']}  _({o['source']})_")

    elif command == "set":
        if len(sys.argv) < 4:
            print("Usage: preferences.py set KEY VALUE")
            return
        key = sys.argv[2]
        value = sys.argv[3]
        prefs.update(key, value, source="user_statement")
        print(f"Saved: *{key}* = {value}")

    else:
        print(f"Unknown command: {command}. Use 'list' or 'set'.")


if __name__ == "__main__":
    main()
