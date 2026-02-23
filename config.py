"""
Portfolio configuration and constants for the governance agent.
"""

from enum import Enum

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
    # International companies — no EDGAR CIK, manual ballot entry only
    "TTE": {
        "cik": None,
        "name": "TotalEnergies",
        "exchange": "Euronext Paris",
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
# Claude model config
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-opus-4-6"
CLAUDE_MAX_TOKENS = 2048

# Max characters of proxy HTML to pass to Claude for extraction
EDGAR_HTML_MAX_CHARS = 60_000

# SEC EDGAR User-Agent (required by SEC policy — change to your contact info)
EDGAR_USER_AGENT = "GovernanceAgent/1.0 personal-use"
