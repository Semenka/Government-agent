"""
LLM-powered proposal analysis.

Uses the backend abstraction (agent.llm_backend) so it works with both
Gemini (cloud) and Anthropic (cloud).

Three main jobs:
  1. extract_ballot_items()    — parse raw proxy text into structured proposals
  2. analyze()                 — single-pass evaluation against user preferences
  3. analyze_with_critique()   — two-pass: analyze, then self-critique.
                                  The second pass is fed the first pass's JSON
                                  and asked to argue the strongest counter-case
                                  before re-deciding.

build_thematic_context() injects portfolio-wide framing (e.g. "you are voting
on climate disclosure at 4 of 18 holdings this season") into prompts so the
LLM can make recommendations that are coherent across the portfolio rather
than evaluating proposals in isolation.
"""

import json
from dataclasses import dataclass, field

from config import ProposalType, VoteChoice
from agent.llm_backend import LLMBackend, get_backend, parse_json_response
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
    raw_json: str = ""           # Verbatim model output (for analysis_passes table)


@dataclass
class CritiqueResult:
    """Output of the self-critique pass.

    `revised` is True iff the second pass's recommendation differs from the
    first pass's. The second pass becomes the stored decision regardless;
    `revised` is recorded so the audit trail is honest about flips.
    """
    final: AnalysisResult
    pass1: AnalysisResult
    critique_text: str
    revised: bool


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
  - title: short descriptive title (80 chars max)
  - description: 1-3 sentence summary of what the proposal does
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
  "confidence": <float 0.0-1.0 how certain you are this is the right vote>,
  "importance": <float 0.0-1.0 how consequential this vote is for the shareholder>,
  "reasoning": "<2-4 sentence explanation>",
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


CRITIQUE_SYSTEM = """You are a skeptical second-opinion reviewer for proxy voting decisions.
Your job is to argue the strongest counter-case to a colleague's draft recommendation,
then decide whether the recommendation should stand or be revised.

You always output valid JSON — never wrap it in markdown code fences.
You are direct, analytical, and willing to disagree.
"""


CRITIQUE_USER_TEMPLATE = """A colleague produced this draft analysis of a shareholder ballot:

DRAFT ANALYSIS (JSON):
{pass1_json}

PROPOSAL CONTEXT:
COMPANY: {company} ({ticker})
PROPOSAL #{proposal_number}: {title}
MANAGEMENT RECOMMENDATION: {management_rec}

USER'S GOVERNANCE PREFERENCES:
{preferences_context}

PORTFOLIO-WIDE THEMATIC CONTEXT:
{thematic_context}

Now do three things:
1. Argue the SINGLE STRONGEST counter-case — what is the best reason the recommendation
   could be wrong? Cite specific governance principles or user preferences in conflict.
2. Decide whether the draft recommendation should stand or be revised.
3. Re-issue the analysis JSON (same schema as the draft) reflecting your final view.

Return a single JSON object with these keys (no code fences):
{{
  "critique": "<2-4 sentence counter-argument>",
  "should_revise": <true|false>,
  "final": {{
    "recommendation": "FOR" | "AGAINST" | "ABSTAIN",
    "confidence": <float 0.0-1.0>,
    "importance": <float 0.0-1.0>,
    "reasoning": "<2-4 sentence explanation, integrating the critique>",
    "governance_concerns": ["..."],
    "aligned_preferences": ["..."],
    "conflicting_factors": ["..."],
    "proposal_type": "<same enum as before>"
  }}
}}
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

        return self._parse_analysis(response.text)

    def analyze_with_critique(
        self,
        ticker: str,
        company_name: str,
        proposal_number: str,
        title: str,
        full_text: str,
        management_rec: str,
        meeting_date: str,
        preferences_context: str,
        thematic_context: str = "",
    ) -> CritiqueResult:
        """
        Two-pass analysis: produce a draft, then a skeptical critique that
        either confirms or revises it. The second pass becomes the final
        decision; both passes are returned so callers can persist the trail.
        """
        pass1 = self.analyze(
            ticker=ticker,
            company_name=company_name,
            proposal_number=proposal_number,
            title=title,
            full_text=full_text,
            management_rec=management_rec,
            meeting_date=meeting_date,
            preferences_context=preferences_context,
        )

        critique_prompt = CRITIQUE_USER_TEMPLATE.format(
            pass1_json=pass1.raw_json or json.dumps(self._result_to_dict(pass1)),
            company=company_name,
            ticker=ticker,
            proposal_number=proposal_number,
            title=title,
            management_rec=management_rec or "not stated",
            preferences_context=preferences_context,
            thematic_context=thematic_context or "(no related portfolio context)",
        )

        response = self.backend.chat(
            system=CRITIQUE_SYSTEM,
            messages=[{"role": "user", "content": critique_prompt}],
        )

        data = parse_json_response(response.text)
        if not isinstance(data, dict) or "final" not in data:
            # Critique pass failed — fall back to pass1, but record the failure
            return CritiqueResult(
                final=pass1,
                pass1=pass1,
                critique_text="(critique pass failed to return valid JSON — using pass-1 result)",
                revised=False,
            )

        final = self._parse_analysis_dict(data.get("final", {}), raw_json=response.text)
        critique_text = str(data.get("critique", ""))[:2000]
        revised = (final.recommendation or "").upper() != (pass1.recommendation or "").upper()

        return CritiqueResult(
            final=final,
            pass1=pass1,
            critique_text=critique_text,
            revised=revised,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_analysis(self, raw_text: str) -> AnalysisResult:
        data = parse_json_response(raw_text)
        if not isinstance(data, dict):
            return AnalysisResult(
                recommendation=VoteChoice.ABSTAIN.value,
                confidence=0.0,
                importance=0.5,
                reasoning="Analysis failed — could not parse LLM response. Manual review required.",
                governance_concerns=["Could not parse response"],
                aligned_preferences=[],
                conflicting_factors=["Parse error"],
                proposal_type=ProposalType.OTHER.value,
                raw_json=raw_text,
            )
        return self._parse_analysis_dict(data, raw_json=raw_text)

    def _parse_analysis_dict(self, data: dict, raw_json: str = "") -> AnalysisResult:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))
        return AnalysisResult(
            recommendation=str(data.get("recommendation", VoteChoice.ABSTAIN.value)).upper(),
            confidence=confidence,
            importance=importance,
            reasoning=data.get("reasoning", ""),
            governance_concerns=list(data.get("governance_concerns", []) or []),
            aligned_preferences=list(data.get("aligned_preferences", []) or []),
            conflicting_factors=list(data.get("conflicting_factors", []) or []),
            proposal_type=data.get("proposal_type", ProposalType.OTHER.value),
            raw_json=raw_json,
        )

    @staticmethod
    def _result_to_dict(result: AnalysisResult) -> dict:
        return {
            "recommendation": result.recommendation,
            "confidence": result.confidence,
            "importance": result.importance,
            "reasoning": result.reasoning,
            "governance_concerns": result.governance_concerns,
            "aligned_preferences": result.aligned_preferences,
            "conflicting_factors": result.conflicting_factors,
            "proposal_type": result.proposal_type,
        }

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


# ---------------------------------------------------------------------------
# Thematic context — portfolio-wide framing for the critique pass
# ---------------------------------------------------------------------------

def build_thematic_context(
    ticker: str,
    proposal_type: str,
    proposal_id: int | None = None,
) -> str:
    """
    Return a short markdown summary of related proposals across the portfolio,
    so the critique pass can spot patterns ("you're voting climate disclosure
    at 4 of your 18 holdings this season") instead of judging in isolation.

    Imported lazily to keep agent.analyzer import-light for callers that don't
    need this (the storage import would otherwise create a soft cycle).
    """
    from data import storage

    lines: list[str] = []

    portfolio_pending = storage.get_pending_by_type_across_portfolio(
        proposal_type, exclude_proposal_id=proposal_id,
    )
    if portfolio_pending:
        lines.append(
            f"- {len(portfolio_pending)} other holding(s) have pending "
            f"`{proposal_type}` proposals this season:"
        )
        for r in portfolio_pending[:8]:
            lines.append(
                f"  • {r['ticker']} — {r['title'][:60]} "
                f"(meeting {r['meeting_date'] or 'unknown'})"
            )

    history = storage.get_proposals_by_ticker_type(
        ticker, proposal_type, exclude_proposal_id=proposal_id,
    )
    if history:
        lines.append(
            f"- Past `{proposal_type}` votes at {ticker.upper()}:"
        )
        for r in history:
            final = r["user_override"] or r["recommendation"] or "—"
            lines.append(
                f"  • {r['meeting_date'] or '?'}: {r['title'][:50]} → {final}"
            )

    if not lines:
        return "(no related proposals found in portfolio history)"
    return "\n".join(lines)
