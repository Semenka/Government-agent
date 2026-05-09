"""
Schema migrations for the governance agent SQLite database.

Forward-only migrations gated by `PRAGMA user_version`. Safe to call repeatedly:
each step is idempotent and skipped once `user_version` has advanced past it.

Called from data.storage.init_db() so existing databases migrate transparently.
"""

import sqlite3


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------
#
# Each step is (target_version, callable). The callable takes a sqlite3.Connection
# and is responsible only for schema work for THAT version.
# Bumping user_version is handled by run_migrations.

def _v1_meeting_and_critique(conn: sqlite3.Connection) -> None:
    """v1: meeting metadata + two-pass analysis traceability."""
    _add_column(conn, "proposals", "vote_deadline", "TEXT")
    _add_column(conn, "proposals", "meeting_url", "TEXT")
    _add_column(conn, "proposals", "extraction_truncated", "INTEGER DEFAULT 0")

    _add_column(conn, "decisions", "peer_iss", "TEXT")
    _add_column(conn, "decisions", "peer_glass_lewis", "TEXT")
    _add_column(conn, "decisions", "critique_text", "TEXT")
    _add_column(conn, "decisions", "critique_revised", "INTEGER DEFAULT 0")
    _add_column(conn, "decisions", "pass1_recommendation", "TEXT")
    _add_column(conn, "decisions", "pass1_reasoning", "TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS meeting_alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id  INTEGER NOT NULL REFERENCES proposals(id),
            tier         TEXT NOT NULL,
            channel      TEXT NOT NULL,
            sent_at      TEXT NOT NULL DEFAULT (datetime('now')),
            message_id   TEXT,
            UNIQUE(proposal_id, tier, channel)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_passes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id     INTEGER NOT NULL REFERENCES decisions(id),
            pass_number     INTEGER NOT NULL,
            recommendation  TEXT,
            confidence      REAL,
            reasoning       TEXT,
            raw_json        TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vote_outcomes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id     INTEGER NOT NULL REFERENCES proposals(id),
            actual_outcome  TEXT,
            support_pct     REAL,
            source          TEXT,
            recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel       TEXT NOT NULL,
            kind          TEXT NOT NULL,
            payload_hash  TEXT NOT NULL,
            sent_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_meeting_date ON proposals(meeting_date)"
    )


MIGRATIONS = [
    (1, _v1_meeting_and_critique),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """ALTER TABLE ADD COLUMN, swallowing the 'duplicate column' error."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def run_migrations(conn: sqlite3.Connection) -> int:
    """
    Apply pending migrations in order. Returns the new user_version.
    Caller is responsible for committing the surrounding transaction.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in MIGRATIONS:
        if current >= target:
            continue
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
        current = target
    return current
