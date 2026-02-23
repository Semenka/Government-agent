"""
Claude-powered proposal analysis.

Two main jobs:
  1. extract_ballot_items() — parse raw proxy text into structured proposals
  2. analyze()              — evaluate a proposal against user preferences
                              and return a scored recommendation
"""

import json
import re
from dataclasses import dataclass

import anthropic

from config import (
    CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS,
    ProposalType,
    VoteChoice,
)
from data.edgar import RawProposal


@dataclass
class AnalysisResult:
    recommendation: str          # FOR / AGAINST / ABSTAIN
    confidence: float            # 0.0 – 1.0
    importance: float            # 0.0 – 1.0
    reasoning: str
    governance_concerns: list[str]
    aligned_preferences: list[str]
    conflicting_factors: list[str]
    proposal_type: str           # Detected / confirmed type


SYSTEM_PROMPT = """You are an expert corporate governance analyst and proxy advisor.
Your role is to evaluate shareholder ballot proposals and recommend how a long-term
investor should vote, based on:
  - Established corporate governance best practices (ISS, Glass Lewis principles)
  - The investor's stated personal preferences (provided in each request)
  - The long-term interests of shareholders

You always output valid JSON — never wrap it in markdown code fences.
You are direct, analytical, and concise.
"""

EXTRACTION_SYSTEM = """You are a document parser. Extract structured ballot items from
proxy statement text. Output a JSON array only — no prose, no code fences."""

EXTRACTION_USER_TEMPLATE = """From the proxy statement text below for {ticker}, extract every
ballot item to be voted on at the annual/special meeting.

For each item return:
  - proposal_number: string (e.g. "1", "2", "3a")
  - title: short descriptive title (≤ 80 chars)
  - description: 1–3 sentence summary of what the proposal does
  - management_recommendation: "FOR", "AGAINST", "ABSTAIN", or "" if not stated

Return a JSON array of objects with these exact keys.
If no ballot items are found, return an empty array [].

PROXY TEXT:
{text}
"""

ANALYSIS_USER_TEMPLATE = """Analyze this shareholder ballot proposal and recommend a vote.

COMPANY: {company} ({ticker})
MEETING DATE: {meeting_date}
PROPOSAL #{proposal_number}: {title}

FULL TEXT:
{full_text}

MANAGEMENT RECOMMENDATION: {management_rec}

USER'S GOVERNANCE PREFERENCES:
{preferences_context}

Evaluate this proposal against governance best practices AND the user's preferences above.

Return a single JSON object with these exact keys (no code fences):
{{
  "recommendation": "FOR" | "AGAINST" | "ABSTAIN",
  "confidence": <float 0.0–1.0 — how certain you are this is the right vote>,
  "importance": <float 0.0–1.0 — how consequential this vote is for the shareholder>,
  "reasoning": "<2–4 sentence explanation>",
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
    def __init__(self, api_key: str | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    # ------------------------------------------------------------------
    # Ballot item extraction from raw proxy text
    # ------------------------------------------------------------------

    def extract_ballot_items(self, text: str, ticker: str) -> list[RawProposal]:
        """Parse raw proxy statement text and return a list of RawProposal objects."""
        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
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
        raw_text = response.content[0].text.strip()
        # Strip accidental code fences
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

        try:
            items = json.loads(raw_text)
        except json.JSONDecodeError:
            return []

        proposals = []
        for item in items:
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
        """
        Evaluate a ballot proposal and return a scored recommendation.
        """
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

        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Graceful fallback if JSON is malformed
            return AnalysisResult(
                recommendation=VoteChoice.ABSTAIN.value,
                confidence=0.0,
                importance=0.5,
                reasoning="Analysis failed — JSON parse error. Manual review required.",
                governance_concerns=["Could not parse Claude response"],
                aligned_preferences=[],
                conflicting_factors=["Parse error"],
                proposal_type=ProposalType.OTHER.value,
            )

        # Clamp floats
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))

        return AnalysisResult(
            recommendation=data.get("recommendation", VoteChoice.ABSTAIN.value),
            confidence=confidence,
            importance=importance,
            reasoning=data.get("reasoning", ""),
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
        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=EXTRACTION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": PREFERENCE_EXTRACTION_TEMPLATE.format(
                        statement=statement
                    ),
                }
            ],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

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

        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        return response.content[0].text
