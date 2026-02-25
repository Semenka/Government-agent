"""
LLM-powered proposal analysis.

Uses the backend abstraction (agent.llm_backend) so it works with both
Ollama (local) and Anthropic (cloud).

Two main jobs:
  1. extract_ballot_items() — parse raw proxy text into structured proposals
  2. analyze()              — evaluate a proposal against user preferences
                              and return a scored recommendation
"""

import json
from dataclasses import dataclass

from config import ProposalType, VoteChoice
from agent.llm_backend import LLMBackend, get_backend, parse_json_response
from data.edgar import RawProposal


@dataclass
class AnalysisResult:
    recommendation: str          # FOR / AGAINST / ABSTAIN
    confidence: float            # 0.0 – 1.0
    importance: float            # 0.0 – 1.0
    reasoning: str
    value_impact: str            # Expected financial impact for shareholders
    governance_concerns: list[str]
    aligned_preferences: list[str]
    conflicting_factors: list[str]
    proposal_type: str           # Detected / confirmed type


SYSTEM_PROMPT = """You are an expert corporate governance analyst and proxy advisor.
Your primary objective is to MAXIMIZE SHAREHOLDER VALUE for a long-term investor.
Every recommendation must be evaluated through the lens of:
  1. Direct financial impact — will this proposal increase or decrease the share price,
     dividends, buybacks, or long-term earnings?
  2. Capital allocation efficiency — does this support optimal use of company resources?
  3. Management accountability — does this align management incentives with shareholders?
  4. Risk-adjusted returns — does this reduce downside risk or improve upside potential?
  5. Established corporate governance best practices (ISS, Glass Lewis principles)
  6. The investor's stated personal preferences (provided in each request)

When management's recommendation conflicts with shareholder value maximization,
always prioritize shareholder value. Be skeptical of proposals that entrench management,
dilute ownership, or waste capital.

You always output valid JSON — never wrap it in markdown code fences.
You are direct, analytical, and concise.
"""

EXTRACTION_SYSTEM = """You are a document parser. Extract structured ballot items from
proxy statement text. Output a JSON array only — no prose, no code fences."""

EXTRACTION_USER_TEMPLATE = """From the proxy statement text below for {ticker}, extract every
ballot item to be voted on at the annual/special meeting.

For each item return:
  - proposal_number: string (e.g. "1", "2", "3a")
  - title: short descriptive title (80 chars max)
  - description: 1-3 sentence summary of what the proposal does
  - management_recommendation: "FOR", "AGAINST", "ABSTAIN", or "" if not stated

Return a JSON array of objects with these exact keys.
If no ballot items are found, return an empty array [].

PROXY TEXT:
{text}
"""

ANALYSIS_USER_TEMPLATE = """Analyze this shareholder ballot proposal and recommend a vote that MAXIMIZES SHAREHOLDER VALUE.

COMPANY: {company} ({ticker})
MEETING DATE: {meeting_date}
PROPOSAL #{proposal_number}: {title}

FULL TEXT:
{full_text}

MANAGEMENT RECOMMENDATION: {management_rec}

USER'S GOVERNANCE PREFERENCES:
{preferences_context}

Your analysis MUST consider:
1. How will this proposal impact shareholder value (share price, dividends, earnings)?
2. Does it improve or weaken capital allocation and management accountability?
3. Does it entrench management, dilute existing shareholders, or destroy value?
4. What is the risk/reward from a long-term shareholder perspective?

Evaluate against governance best practices AND the user's preferences. Always choose the
vote that best serves the shareholder's financial interest.

Return a single JSON object with these exact keys (no code fences):
{{
  "recommendation": "FOR" | "AGAINST" | "ABSTAIN",
  "confidence": <float 0.0-1.0 how certain you are this is the right vote>,
  "importance": <float 0.0-1.0 how consequential this vote is for the shareholder>,
  "reasoning": "<2-4 sentence explanation focused on shareholder value impact>",
  "value_impact": "<1 sentence on expected financial impact for shareholders>",
  "governance_concerns": ["<specific concern 1>", ...],
  "aligned_preferences": ["<which user preference supports this recommendation>", ...],
  "conflicting_factors": ["<anything that creates uncertainty>", ...],
  "proposal_type": "<one of: director_election | executive_compensation | auditor_ratification | equity_plan | shareholder_proposal_esg | shareholder_proposal_governance | merger_acquisition | charter_bylaw_amendment | other>"
}}
"""

PREFERENCE_EXTRACTION_TEMPLATE = """The user made the following statement about their governance preferences:

"{statement}"

Extract any specific, actionable voting preferences from this statement.
Return a JSON array of objects, each with:
  - key: dot-notation preference key (e.g. "director_elections.vote_against_low_attendance")
  - value: the value (true/false/number/string)
  - description: one-sentence explanation of what was inferred

Return [] if no clear preferences can be extracted.
"""


class ProposalAnalyzer:
    def __init__(self, backend: LLMBackend | None = None) -> None:
        self.backend = backend or get_backend()

    # ------------------------------------------------------------------
    # Ballot item extraction from raw proxy text
    # ------------------------------------------------------------------

    def extract_ballot_items(self, text: str, ticker: str) -> list[RawProposal]:
        """Parse raw proxy statement text and return a list of RawProposal objects."""
        response = self.backend.chat(
            system=EXTRACTION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_USER_TEMPLATE.format(
                        ticker=ticker, text=text
                    ),
                }
            ],
        )
        items = parse_json_response(response.text)
        if not isinstance(items, list):
            return []

        proposals = []
        for item in items:
            if not isinstance(item, dict):
                continue
            proposals.append(
                RawProposal(
                    proposal_number=str(item.get("proposal_number", "")),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    management_recommendation=item.get("management_recommendation", ""),
                )
            )
        return proposals

    # ------------------------------------------------------------------
    # Proposal analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        ticker: str,
        company_name: str,
        proposal_number: str,
        title: str,
        full_text: str,
        management_rec: str,
        meeting_date: str,
        preferences_context: str,
    ) -> AnalysisResult:
        """Evaluate a ballot proposal and return a scored recommendation."""
        prompt = ANALYSIS_USER_TEMPLATE.format(
            company=company_name,
            ticker=ticker,
            meeting_date=meeting_date or "unknown",
            proposal_number=proposal_number,
            title=title,
            full_text=full_text or title,
            management_rec=management_rec or "not stated",
            preferences_context=preferences_context,
        )

        response = self.backend.chat(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        data = parse_json_response(response.text)
        if not isinstance(data, dict):
            return AnalysisResult(
                recommendation=VoteChoice.ABSTAIN.value,
                confidence=0.0,
                importance=0.5,
                reasoning="Analysis failed — could not parse LLM response. Manual review required.",
                value_impact="Unknown — parse error.",
                governance_concerns=["Could not parse response"],
                aligned_preferences=[],
                conflicting_factors=["Parse error"],
                proposal_type=ProposalType.OTHER.value,
            )

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))

        return AnalysisResult(
            recommendation=data.get("recommendation", VoteChoice.ABSTAIN.value),
            confidence=confidence,
            importance=importance,
            reasoning=data.get("reasoning", ""),
            value_impact=data.get("value_impact", ""),
            governance_concerns=data.get("governance_concerns", []),
            aligned_preferences=data.get("aligned_preferences", []),
            conflicting_factors=data.get("conflicting_factors", []),
            proposal_type=data.get("proposal_type", ProposalType.OTHER.value),
        )

    # ------------------------------------------------------------------
    # Preference learning from natural-language statements
    # ------------------------------------------------------------------

    def extract_preferences_from_statement(self, statement: str) -> list[dict]:
        """
        Given a natural language statement, extract structured preference updates.
        Returns a list of {key, value, description} dicts.
        """
        response = self.backend.chat(
            system=EXTRACTION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": PREFERENCE_EXTRACTION_TEMPLATE.format(
                        statement=statement
                    ),
                }
            ],
            max_tokens=512,
        )
        result = parse_json_response(response.text)
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Conversational Q&A
    # ------------------------------------------------------------------

    def chat(
        self,
        user_message: str,
        history: list[dict],
        preferences_context: str,
    ) -> str:
        """
        Answer a freeform question about governance or the portfolio.
        history: list of {role, content} dicts
        """
        system = (
            SYSTEM_PROMPT
            + "\n\nUser's current governance preferences:\n"
            + preferences_context
        )
        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": user_message})

        response = self.backend.chat(
            system=system,
            messages=messages,
            max_tokens=1024,
        )
        return response.text
