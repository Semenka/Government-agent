"""
Portfolio configuration and constants for the governance agent.
"""

import os
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Portfolio companies
# CIK numbers for US-listed companies come from SEC EDGAR.
# International companies (TTE, WISE, 1810.HK) require manual ballot entry.
# ---------------------------------------------------------------------------

PORTFOLIO: dict[str, dict] = {
    # US companies with SEC EDGAR CIKs
    "SYF": {
        "cik": "0001601712",
        "name": "Synchrony Financial",
        "exchange": "NYSE",
    },
    "OXY": {
        "cik": "0000797468",
        "name": "Occidental Petroleum",
        "exchange": "NYSE",
    },
    "BABA": {
        "cik": "0001577552",
        "name": "Alibaba Group (ADR)",
        "exchange": "NYSE",
    },
    "BIDU": {
        "cik": "0001100010",
        "name": "Baidu (ADR)",
        "exchange": "NASDAQ",
    },
    "TSLA": {
        "cik": "0001318605",
        "name": "Tesla",
        "exchange": "NASDAQ",
    },
    "GOOGL": {
        "cik": "0001652044",
        "name": "Alphabet",
        "exchange": "NASDAQ",
    },
    "NVDA": {
        "cik": "0001045810",
        "name": "NVIDIA",
        "exchange": "NASDAQ",
    },
    "DOYU": {
        "cik": "0001774340",
        "name": "DouYu International (ADR)",
        "exchange": "NASDAQ",
    },
    "AAL": {
        "cik": "0000006201",
        "name": "American Airlines",
        "exchange": "NASDAQ",
    },
    "USB": {
        "cik": "0000036104",
        "name": "US Bancorp",
        "exchange": "NYSE",
    },
    "STZ": {
        "cik": "0000016160",
        "name": "Constellation Brands",
        "exchange": "NYSE",
    },
    "POOL": {
        "cik": "0000945841",
        "name": "Pool Corporation",
        "exchange": "NASDAQ",
    },
    "LEN": {
        "cik": "0000920760",
        "name": "Lennar",
        "exchange": "NYSE",
    },
    "UNH": {
        "cik": "0000731766",
        "name": "UnitedHealth Group",
        "exchange": "NYSE",
    },
    # TotalEnergies — French company, also listed on NYSE as ADR.
    # Files 20-F and 6-K with SEC (foreign private issuer, no DEF 14A).
    "TTE": {
        "cik": "0000879764",
        "name": "TotalEnergies",
        "exchange": "NYSE",
        "is_foreign_private_issuer": True,
        "ir_url": "https://totalenergies.com/investors",
    },
    "WISE": {
        "cik": None,
        "name": "Wise PLC",
        "exchange": "LSE",
        "ir_url": "https://investors.wise.com",
    },
    "1810.HK": {
        "cik": None,
        "name": "Xiaomi",
        "exchange": "HKEX",
        "ir_url": "https://ir.mi.com",
    },
}

# Tickers that have a CIK (can be fetched from EDGAR)
EDGAR_TICKERS = [t for t, v in PORTFOLIO.items() if v["cik"] is not None]

# Tickers requiring manual ballot entry
MANUAL_TICKERS = [t for t, v in PORTFOLIO.items() if v["cik"] is None]


# ---------------------------------------------------------------------------
# Proposal type classification
# ---------------------------------------------------------------------------

class ProposalType(str, Enum):
    DIRECTOR_ELECTION = "director_election"
    EXECUTIVE_COMPENSATION = "executive_compensation"
    AUDITOR_RATIFICATION = "auditor_ratification"
    EQUITY_PLAN = "equity_plan"
    SHAREHOLDER_PROPOSAL_ESG = "shareholder_proposal_esg"
    SHAREHOLDER_PROPOSAL_GOVERNANCE = "shareholder_proposal_governance"
    MERGER_ACQUISITION = "merger_acquisition"
    CHARTER_BYLAW_AMENDMENT = "charter_bylaw_amendment"
    OTHER = "other"


class VoteChoice(str, Enum):
    FOR = "FOR"
    AGAINST = "AGAINST"
    ABSTAIN = "ABSTAIN"
    WITHHOLD = "WITHHOLD"  # Used in some director elections


class ProposalStatus(str, Enum):
    PENDING = "pending"
    ANALYZED = "analyzed"
    VOTED = "voted"


class ProposalSource(str, Enum):
    EDGAR = "edgar"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Escalation thresholds
# ---------------------------------------------------------------------------

# Escalate to user when BOTH conditions are true
ESCALATION_CONFIDENCE_THRESHOLD = 0.70   # confidence below this
ESCALATION_IMPORTANCE_THRESHOLD = 0.60   # importance above this

# Proposal types that are ALWAYS escalated regardless of confidence
ALWAYS_ESCALATE_TYPES = {
    ProposalType.MERGER_ACQUISITION,
    ProposalType.CHARTER_BYLAW_AMENDMENT,
    ProposalType.EQUITY_PLAN,
}

# Escalate executive comp if importance above this
EXEC_COMP_IMPORTANCE_ESCALATE = 0.80


# ---------------------------------------------------------------------------
# LLM backend config
# ---------------------------------------------------------------------------

# "anthropic" | "gemini" | "local" | "ollama"
LLM_BACKEND = os.getenv("LLM_BACKEND", "anthropic")

# Anthropic
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MAX_TOKENS = 2048

# Gemini (Google AI)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Local LLM (OpenAI-compatible /v1/chat/completions — gbrain, LM Studio,
# llama-server, vLLM, Jan, LocalAI, Ollama's OpenAI-compat shim, …)
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "")
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "")

# Ollama (native /api/chat)
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")


# ---------------------------------------------------------------------------
# EDGAR config
# ---------------------------------------------------------------------------

# Max characters of proxy HTML to pass to LLM for extraction.
# Bumped from 60k to 120k so we don't silently drop the tail of long proxies.
EDGAR_HTML_MAX_CHARS = int(os.getenv("EDGAR_HTML_MAX_CHARS", "120000"))

# SEC EDGAR User-Agent (required by SEC policy — change to your contact info)
EDGAR_USER_AGENT = os.getenv(
    "EDGAR_USER_AGENT",
    "GovernanceAgent/1.0 personal-use"
)

# Local cache directory for EDGAR downloads
EDGAR_CACHE_DIR = os.getenv("EDGAR_CACHE_DIR", ".cache/edgar")


# ---------------------------------------------------------------------------
# Scheduler + notifier config
# ---------------------------------------------------------------------------

# Weekly digest time (24h format)
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "08:00")
SCHEDULE_DAY = os.getenv("SCHEDULE_DAY", "monday")

# Daily meeting-aware fan-out time
MEETING_CHECK_TIME = os.getenv("MEETING_CHECK_TIME", "07:00")

# Days-out tiers for pre-meeting alerts. Each (proposal, tier, channel)
# combination fires exactly once via the meeting_alerts dedupe table.
ALERT_TIERS = [
    int(x) for x in os.getenv("ALERT_TIERS", "14,7,3,1").split(",") if x.strip()
]

# Comma-separated active notifier channels: "telegram,email" or "telegram"
# Empty falls back to telegram if a bot token is configured, else email.
NOTIFIER_CHANNELS = os.getenv("NOTIFIER_CHANNELS", "")

# Telegram bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Email (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
