"""
SQLite database layer for the governance agent.

Tables:
  proposals        – ballot items from proxy filings or manual entry
  decisions        – AI recommendations and user overrides
  preferences      – user governance preferences (key/value)
  conversation_history – dialog stored for preference learning
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./governance.db")


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist yet, and run migrations."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS proposals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                company_name    TEXT NOT NULL,
                meeting_date    TEXT,
                filing_date     TEXT,
                accession_number TEXT,
                proposal_number TEXT NOT NULL,
                title           TEXT NOT NULL,
                full_text       TEXT,
                management_rec  TEXT,
                proposal_type   TEXT NOT NULL DEFAULT 'other',
                source          TEXT NOT NULL DEFAULT 'edgar',
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id         INTEGER NOT NULL REFERENCES proposals(id),
                recommendation      TEXT NOT NULL,
                confidence          REAL NOT NULL,
                importance          REAL NOT NULL,
                reasoning           TEXT,
                value_impact        TEXT,
                governance_concerns TEXT,
                aligned_preferences TEXT,
                conflicting_factors TEXT,
                needs_review        INTEGER NOT NULL DEFAULT 0,
                user_override       TEXT,
                user_note           TEXT,
                decided_at          TEXT NOT NULL DEFAULT (datetime('now')),
                overridden_at       TEXT,
                UNIQUE(proposal_id)
            );

            CREATE TABLE IF NOT EXISTS preferences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'user_statement',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conversation_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Migration: add value_impact column to existing databases
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()
        ]
        if "value_impact" not in columns:
            conn.execute("ALTER TABLE decisions ADD COLUMN value_impact TEXT")


# ---------------------------------------------------------------------------
# Proposal CRUD
# ---------------------------------------------------------------------------

def upsert_proposal(
    ticker: str,
    company_name: str,
    proposal_number: str,
    title: str,
    full_text: str = "",
    management_rec: str = "",
    proposal_type: str = "other",
    source: str = "edgar",
    meeting_date: str = "",
    filing_date: str = "",
    accession_number: str = "",
) -> int:
    """Insert a proposal, or update if (ticker, accession_number, proposal_number) already exists."""
    with get_conn() as conn:
        existing = conn.execute(
            """SELECT id FROM proposals
               WHERE ticker=? AND proposal_number=?
               AND (accession_number=? OR (accession_number IS NULL AND ?=''))""",
            (ticker, proposal_number, accession_number, accession_number),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE proposals SET title=?, full_text=?, management_rec=?,
                   proposal_type=?, meeting_date=?, filing_date=?
                   WHERE id=?""",
                (title, full_text, management_rec, proposal_type,
                 meeting_date, filing_date, existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO proposals
               (ticker, company_name, meeting_date, filing_date,
                accession_number, proposal_number, title, full_text,
                management_rec, proposal_type, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, company_name, meeting_date, filing_date,
             accession_number, proposal_number, title, full_text,
             management_rec, proposal_type, source),
        )
        return cur.lastrowid


def get_pending_proposals(ticker: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if ticker:
            return conn.execute(
                "SELECT * FROM proposals WHERE status='pending' AND ticker=? ORDER BY ticker, meeting_date, CAST(proposal_number AS INTEGER)",
                (ticker.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM proposals WHERE status='pending' ORDER BY ticker, meeting_date, CAST(proposal_number AS INTEGER)"
        ).fetchall()


def get_all_proposals(ticker: str | None = None, year: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        clauses = []
        params: list = []
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker.upper())
        if year:
            clauses.append("(meeting_date LIKE ? OR filing_date LIKE ?)")
            params.extend([f"{year}%", f"{year}%"])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return conn.execute(
            f"SELECT * FROM proposals {where} ORDER BY ticker, meeting_date, CAST(proposal_number AS INTEGER)",
            params,
        ).fetchall()


def mark_proposal_analyzed(proposal_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='analyzed' WHERE id=?", (proposal_id,)
        )


def mark_proposal_voted(proposal_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='voted' WHERE id=?", (proposal_id,)
        )


# ---------------------------------------------------------------------------
# Decision CRUD
# ---------------------------------------------------------------------------

def save_decision(
    proposal_id: int,
    recommendation: str,
    confidence: float,
    importance: float,
    reasoning: str = "",
    value_impact: str = "",
    governance_concerns: list | None = None,
    aligned_preferences: list | None = None,
    conflicting_factors: list | None = None,
    needs_review: bool = False,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO decisions
               (proposal_id, recommendation, confidence, importance, reasoning,
                value_impact, governance_concerns, aligned_preferences,
                conflicting_factors, needs_review, decided_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                proposal_id,
                recommendation,
                confidence,
                importance,
                reasoning,
                value_impact,
                json.dumps(governance_concerns or []),
                json.dumps(aligned_preferences or []),
                json.dumps(conflicting_factors or []),
                int(needs_review),
            ),
        )
        return cur.lastrowid


def get_review_queue() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT p.*, d.id as decision_id, d.recommendation, d.confidence,
                      d.importance, d.reasoning, d.value_impact,
                      d.governance_concerns,
                      d.aligned_preferences, d.conflicting_factors
               FROM proposals p
               JOIN decisions d ON d.proposal_id = p.id
               WHERE d.needs_review=1 AND d.user_override IS NULL
               ORDER BY d.importance DESC, p.ticker""",
        ).fetchall()


def apply_user_override(decision_id: int, vote: str, note: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE decisions
               SET user_override=?, user_note=?, needs_review=0,
                   overridden_at=datetime('now')
               WHERE id=?""",
            (vote.upper(), note, decision_id),
        )
        # Also mark parent proposal as voted
        row = conn.execute(
            "SELECT proposal_id FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE proposals SET status='voted' WHERE id=?",
                (row["proposal_id"],),
            )


def get_report_rows(ticker: str | None = None, year: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        clauses = []
        params: list = []
        if ticker:
            clauses.append("p.ticker=?")
            params.append(ticker.upper())
        if year:
            clauses.append("(p.meeting_date LIKE ? OR p.filing_date LIKE ?)")
            params.extend([f"{year}%", f"{year}%"])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return conn.execute(
            f"""SELECT p.ticker, p.company_name, p.meeting_date,
                       p.proposal_number, p.title, p.proposal_type,
                       p.management_rec, p.status,
                       d.recommendation, d.confidence, d.importance,
                       d.needs_review, d.user_override, d.user_note,
                       d.reasoning, d.value_impact
                FROM proposals p
                LEFT JOIN decisions d ON d.proposal_id = p.id
                {where}
                ORDER BY p.ticker, p.meeting_date, CAST(p.proposal_number AS INTEGER)""",
            params,
        ).fetchall()


# ---------------------------------------------------------------------------
# Preferences CRUD
# ---------------------------------------------------------------------------

def save_preference(key: str, value: str, source: str = "user_statement") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO preferences (key, value, source) VALUES (?,?,?)",
            (key, value, source),
        )


def get_all_preferences() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM preferences ORDER BY key, created_at DESC"
        ).fetchall()


def get_latest_preferences() -> list[sqlite3.Row]:
    """Return the most recent preference entry per key."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT p1.* FROM preferences p1
               WHERE p1.created_at = (
                   SELECT MAX(p2.created_at) FROM preferences p2
                   WHERE p2.key = p1.key
               )
               ORDER BY p1.key""",
        ).fetchall()


# ---------------------------------------------------------------------------
# Conversation history CRUD
# ---------------------------------------------------------------------------

def add_conversation_message(role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversation_history (role, content) VALUES (?,?)",
            (role, content),
        )


def get_conversation_history(limit: int = 20) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM conversation_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
