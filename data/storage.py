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
    """Create tables if they don't exist yet, then run forward migrations."""
    from data.migrations import run_migrations

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
        run_migrations(conn)


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
    vote_deadline: str = "",
    meeting_url: str = "",
    extraction_truncated: bool = False,
) -> int:
    """Insert a proposal, or update if (ticker, accession_number, proposal_number) already exists.

    Meeting metadata fields (`meeting_date`, `vote_deadline`, `meeting_url`) only overwrite
    the existing row's value when a non-empty replacement is provided — so a later fetch
    that happens to lack a parsed date won't blank out a previously captured one.
    """
    with get_conn() as conn:
        existing = conn.execute(
            """SELECT id FROM proposals
               WHERE ticker=? AND proposal_number=?
               AND (accession_number=? OR (accession_number IS NULL AND ?=''))""",
            (ticker, proposal_number, accession_number, accession_number),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE proposals SET
                       title=?,
                       full_text=?,
                       management_rec=?,
                       proposal_type=?,
                       meeting_date=COALESCE(NULLIF(?, ''), meeting_date),
                       filing_date=COALESCE(NULLIF(?, ''), filing_date),
                       vote_deadline=COALESCE(NULLIF(?, ''), vote_deadline),
                       meeting_url=COALESCE(NULLIF(?, ''), meeting_url),
                       extraction_truncated=?
                   WHERE id=?""",
                (
                    title, full_text, management_rec, proposal_type,
                    meeting_date, filing_date, vote_deadline, meeting_url,
                    int(bool(extraction_truncated)),
                    existing["id"],
                ),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO proposals
               (ticker, company_name, meeting_date, filing_date,
                accession_number, proposal_number, title, full_text,
                management_rec, proposal_type, source,
                vote_deadline, meeting_url, extraction_truncated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, company_name, meeting_date, filing_date,
                accession_number, proposal_number, title, full_text,
                management_rec, proposal_type, source,
                vote_deadline, meeting_url, int(bool(extraction_truncated)),
            ),
        )
        return cur.lastrowid


def update_proposal_meeting_metadata(
    proposal_id: int,
    meeting_date: str = "",
    vote_deadline: str = "",
    meeting_url: str = "",
) -> None:
    """Backfill meeting metadata onto an existing proposal (only writes non-empty values)."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE proposals SET
                   meeting_date=COALESCE(NULLIF(?, ''), meeting_date),
                   vote_deadline=COALESCE(NULLIF(?, ''), vote_deadline),
                   meeting_url=COALESCE(NULLIF(?, ''), meeting_url)
               WHERE id=?""",
            (meeting_date, vote_deadline, meeting_url, proposal_id),
        )


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
    governance_concerns: list | None = None,
    aligned_preferences: list | None = None,
    conflicting_factors: list | None = None,
    needs_review: bool = False,
    pass1_recommendation: str | None = None,
    pass1_reasoning: str | None = None,
    critique_text: str | None = None,
    critique_revised: bool = False,
) -> int:
    """Insert or replace the decision row for a proposal.

    Returns the decision id (whether new or replaced), looked up by proposal_id —
    INSERT OR REPLACE rewrites the row, so we re-query rather than rely on lastrowid.
    """
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (proposal_id, recommendation, confidence, importance, reasoning,
                governance_concerns, aligned_preferences, conflicting_factors,
                needs_review,
                pass1_recommendation, pass1_reasoning,
                critique_text, critique_revised,
                decided_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                proposal_id,
                recommendation,
                confidence,
                importance,
                reasoning,
                json.dumps(governance_concerns or []),
                json.dumps(aligned_preferences or []),
                json.dumps(conflicting_factors or []),
                int(needs_review),
                pass1_recommendation,
                pass1_reasoning,
                critique_text,
                int(bool(critique_revised)),
            ),
        )
        row = conn.execute(
            "SELECT id FROM decisions WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        return row["id"] if row else 0


def get_decision_for_proposal(proposal_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM decisions WHERE proposal_id=?", (proposal_id,)
        ).fetchone()


def get_decision_with_proposal(decision_id: int) -> sqlite3.Row | None:
    """Look up a decision joined with its proposal — used by Telegram callbacks
    and any other path where the queue scan in DecisionEngine.apply_user_vote
    is not appropriate.
    """
    with get_conn() as conn:
        return conn.execute(
            """SELECT p.*, d.id AS decision_id, d.recommendation, d.confidence,
                      d.importance, d.reasoning, d.governance_concerns,
                      d.aligned_preferences, d.conflicting_factors,
                      d.user_override, d.user_note, d.needs_review
               FROM decisions d
               JOIN proposals p ON p.id = d.proposal_id
               WHERE d.id=?""",
            (decision_id,),
        ).fetchone()


def get_review_queue() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT p.*, d.id as decision_id, d.recommendation, d.confidence,
                      d.importance, d.reasoning, d.governance_concerns,
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
                       d.reasoning
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


# ---------------------------------------------------------------------------
# Meeting-window queries (for meeting-aware scheduler)
# ---------------------------------------------------------------------------

def get_proposals_by_meeting_window(
    min_days: int,
    max_days: int,
    today: str | None = None,
) -> list[sqlite3.Row]:
    """
    Return proposals whose meeting_date falls between today+min_days and today+max_days
    (inclusive on both ends), excluding already-voted ones. Both endpoints are ISO dates.

    Used by scheduler.run_meeting_check to discover proposals approaching their meeting.
    """
    with get_conn() as conn:
        if today is None:
            today_iso = datetime.now().strftime("%Y-%m-%d")
        else:
            today_iso = today
        return conn.execute(
            """SELECT p.*, d.id AS decision_id, d.recommendation, d.confidence,
                      d.importance, d.reasoning, d.user_override, d.needs_review
               FROM proposals p
               LEFT JOIN decisions d ON d.proposal_id = p.id
               WHERE p.meeting_date IS NOT NULL
                 AND p.meeting_date != ''
                 AND p.status != 'voted'
                 AND date(p.meeting_date) BETWEEN date(?, ? || ' days')
                                              AND date(?, ? || ' days')
               ORDER BY p.meeting_date, p.ticker, CAST(p.proposal_number AS INTEGER)""",
            (today_iso, f"+{min_days}", today_iso, f"+{max_days}"),
        ).fetchall()


def get_proposals_by_ticker_type(
    ticker: str,
    proposal_type: str,
    exclude_proposal_id: int | None = None,
) -> list[sqlite3.Row]:
    """For thematic context: 'how have we voted on this proposal type at this ticker?'"""
    with get_conn() as conn:
        params: list = [ticker.upper(), proposal_type]
        sql = """SELECT p.*, d.recommendation, d.user_override
                 FROM proposals p
                 LEFT JOIN decisions d ON d.proposal_id = p.id
                 WHERE p.ticker=? AND p.proposal_type=?"""
        if exclude_proposal_id:
            sql += " AND p.id != ?"
            params.append(exclude_proposal_id)
        sql += " ORDER BY p.meeting_date DESC LIMIT 5"
        return conn.execute(sql, params).fetchall()


def get_pending_by_type_across_portfolio(
    proposal_type: str,
    exclude_proposal_id: int | None = None,
) -> list[sqlite3.Row]:
    """For thematic context: 'how many holdings are voting on this same theme right now?'"""
    with get_conn() as conn:
        params: list = [proposal_type]
        sql = """SELECT ticker, company_name, title, meeting_date
                 FROM proposals
                 WHERE proposal_type=? AND status != 'voted'"""
        if exclude_proposal_id:
            sql += " AND id != ?"
            params.append(exclude_proposal_id)
        sql += " ORDER BY meeting_date, ticker LIMIT 20"
        return conn.execute(sql, params).fetchall()


def list_upcoming_meetings(days_ahead: int = 30) -> list[sqlite3.Row]:
    """Distinct ticker / meeting_date pairs in the next `days_ahead` days."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT ticker, company_name, meeting_date,
                      COUNT(*) AS proposal_count,
                      SUM(CASE WHEN status='voted' THEN 1 ELSE 0 END) AS voted_count
               FROM proposals
               WHERE meeting_date IS NOT NULL AND meeting_date != ''
                 AND date(meeting_date) BETWEEN date('now') AND date('now', ? || ' days')
               GROUP BY ticker, meeting_date
               ORDER BY meeting_date, ticker""",
            (f"+{days_ahead}",),
        ).fetchall()


# ---------------------------------------------------------------------------
# Meeting alerts (dedupe key: proposal_id + tier + channel)
# ---------------------------------------------------------------------------

def meeting_alert_exists(proposal_id: int, tier: str, channel: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM meeting_alerts
               WHERE proposal_id=? AND tier=? AND channel=? LIMIT 1""",
            (proposal_id, tier, channel),
        ).fetchone()
        return row is not None


def record_meeting_alert(
    proposal_id: int,
    tier: str,
    channel: str,
    message_id: str = "",
) -> int | None:
    """Record an alert. Returns the new row id, or None if it already existed."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO meeting_alerts (proposal_id, tier, channel, message_id)
                   VALUES (?,?,?,?)""",
                (proposal_id, tier, channel, message_id),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


# ---------------------------------------------------------------------------
# Analysis passes (full traceability for two-pass critique)
# ---------------------------------------------------------------------------

def save_analysis_pass(
    decision_id: int,
    pass_number: int,
    recommendation: str,
    confidence: float,
    reasoning: str,
    raw_json: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO analysis_passes
               (decision_id, pass_number, recommendation, confidence, reasoning, raw_json)
               VALUES (?,?,?,?,?,?)""",
            (decision_id, pass_number, recommendation, confidence, reasoning, raw_json),
        )
        return cur.lastrowid


def get_analysis_passes(decision_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM analysis_passes
               WHERE decision_id=? ORDER BY pass_number""",
            (decision_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Vote outcomes (post-meeting accuracy tracking)
# ---------------------------------------------------------------------------

def record_vote_outcome(
    proposal_id: int,
    actual_outcome: str,
    support_pct: float | None = None,
    source: str = "manual",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO vote_outcomes
               (proposal_id, actual_outcome, support_pct, source)
               VALUES (?,?,?,?)""",
            (proposal_id, actual_outcome, support_pct, source),
        )
        return cur.lastrowid


def get_accuracy_summary() -> dict:
    """
    Compare our recommendations against recorded shareholder vote outcomes.
    Returns counts of agreements / disagreements where outcome data exists.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.recommendation, vo.actual_outcome
               FROM decisions d
               JOIN vote_outcomes vo ON vo.proposal_id = d.proposal_id"""
        ).fetchall()

    total = len(rows)
    agree = sum(
        1 for r in rows
        if r["recommendation"] and r["actual_outcome"]
        and r["recommendation"].upper() == r["actual_outcome"].upper()
    )
    return {
        "total_with_outcome": total,
        "agree_with_outcome": agree,
        "agreement_pct": (agree / total) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Notification log (cross-channel dedupe by content hash)
# ---------------------------------------------------------------------------

def notification_seen(channel: str, kind: str, payload_hash: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM notification_log
               WHERE channel=? AND kind=? AND payload_hash=? LIMIT 1""",
            (channel, kind, payload_hash),
        ).fetchone()
        return row is not None


def log_notification(channel: str, kind: str, payload_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO notification_log (channel, kind, payload_hash)
               VALUES (?,?,?)""",
            (channel, kind, payload_hash),
        )
