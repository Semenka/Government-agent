"""
Helpers for manually entering ballot items for international companies
(TTE – Euronext Paris, WISE – LSE, 1810.HK – HKEX) that are not
available via SEC EDGAR.
"""

from config import PORTFOLIO, MANUAL_TICKERS, ProposalType
from data import storage


def prompt_proposal_entry(ticker: str) -> dict | None:
    """
    Interactive CLI prompt to enter one ballot item.
    Returns a dict of field values or None if the user aborts.

    This function is called from ui/cli.py where `rich` handles display;
    here we use plain input() so that the function stays dependency-free.
    """
    company = PORTFOLIO.get(ticker, {})
    company_name = company.get("name", ticker)

    print(f"\nEntering ballot item for {ticker} ({company_name})")
    print("Press Ctrl-C or leave 'proposal_number' blank to cancel.\n")

    meeting_date = input("Meeting date (YYYY-MM-DD, or blank): ").strip()
    proposal_number = input("Proposal # (e.g. 1, 2a, 3): ").strip()
    if not proposal_number:
        return None

    title = input("Proposal title (short): ").strip()
    if not title:
        return None

    print("Proposal types:")
    types = list(ProposalType)
    for i, pt in enumerate(types, 1):
        print(f"  {i}. {pt.value}")
    type_choice = input(f"Type [1-{len(types)}] (default 10=other): ").strip()
    try:
        proposal_type = types[int(type_choice) - 1].value
    except (ValueError, IndexError):
        proposal_type = ProposalType.OTHER.value

    full_text = input("Paste proposal description (optional, press Enter twice when done):\n")
    # Allow multi-line: keep reading until blank line
    lines = [full_text]
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    full_text = "\n".join(lines).strip()

    management_rec = input("Management recommendation [FOR/AGAINST/ABSTAIN or blank]: ").strip().upper()

    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "meeting_date": meeting_date,
        "proposal_number": proposal_number,
        "title": title,
        "full_text": full_text,
        "management_rec": management_rec,
        "proposal_type": proposal_type,
        "source": "manual",
    }


def save_manual_proposal(fields: dict) -> int:
    """Persist a manually entered proposal; returns the new row id."""
    return storage.upsert_proposal(
        ticker=fields["ticker"],
        company_name=fields["company_name"],
        proposal_number=fields["proposal_number"],
        title=fields["title"],
        full_text=fields.get("full_text", ""),
        management_rec=fields.get("management_rec", ""),
        proposal_type=fields.get("proposal_type", ProposalType.OTHER.value),
        source="manual",
        meeting_date=fields.get("meeting_date", ""),
        filing_date="",
        accession_number="",
    )


def get_ir_hint(ticker: str) -> str:
    """Return a helpful IR page URL for the given ticker."""
    company = PORTFOLIO.get(ticker, {})
    ir_url = company.get("ir_url", "")
    name = company.get("name", ticker)
    exchange = company.get("exchange", "")
    if ir_url:
        return f"{name} ({exchange}) — IR page: {ir_url}"
    return f"{name} ({exchange}) — check company IR website for proxy materials"
