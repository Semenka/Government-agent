"""
Decision engine — orchestrates analysis, applies escalation rules,
and coordinates between the analyzer, preference engine, and storage.

Optimized with:
  - Parallel proposal analysis via ThreadPoolExecutor
  - Pre-computed preferences context (loaded once per batch)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from config import (
    ALWAYS_ESCALATE_TYPES,
    ESCALATION_CONFIDENCE_THRESHOLD,
    ESCALATION_IMPORTANCE_THRESHOLD,
    EXEC_COMP_IMPORTANCE_ESCALATE,
    ProposalType,
    VoteChoice,
)
from agent.analyzer import ProposalAnalyzer, AnalysisResult
from agent.preference_engine import PreferenceEngine
from data import storage


@dataclass
class ProcessingReport:
    total: int
    analyzed: int
    auto_decided: int
    escalated: int
    errors: list[str] = field(default_factory=list)


class DecisionEngine:
    def __init__(self) -> None:
        self.analyzer = ProposalAnalyzer()
        self.prefs = PreferenceEngine()

    # ------------------------------------------------------------------
    # Main processing loop (parallelized)
    # ------------------------------------------------------------------

    def process_all_pending(
        self,
        ticker: str | None = None,
        verbose: bool = True,
        max_workers: int = 4,
    ) -> ProcessingReport:
        """
        Analyze all pending proposals (optionally filtered by ticker).
        Uses thread-level parallelism for LLM calls.
        """
        proposals = storage.get_pending_proposals(ticker=ticker)
        report = ProcessingReport(
            total=len(proposals), analyzed=0, auto_decided=0, escalated=0,
        )

        if not proposals:
            return report

        # Load preferences once for the entire batch
        preferences_context = self.prefs.get_context()

        def _analyze_one(row):
            result = self.analyzer.analyze(
                ticker=row["ticker"],
                company_name=row["company_name"],
                proposal_number=row["proposal_number"],
                title=row["title"],
                full_text=row["full_text"] or "",
                management_rec=row["management_rec"] or "",
                meeting_date=row["meeting_date"] or "",
                preferences_context=preferences_context,
            )
            return row, result

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_analyze_one, row): row for row in proposals
            }

            for future in as_completed(futures):
                row = futures[future]
                try:
                    _, result = future.result()
                except Exception as exc:
                    err = f"[{row['ticker']} #{row['proposal_number']}] {exc}"
                    report.errors.append(err)
                    if verbose:
                        print(f"  ERROR: {err}")
                    continue

                needs_review = self._should_escalate(result)

                storage.save_decision(
                    proposal_id=row["id"],
                    recommendation=result.recommendation,
                    confidence=result.confidence,
                    importance=result.importance,
                    reasoning=result.reasoning,
                    governance_concerns=result.governance_concerns,
                    aligned_preferences=result.aligned_preferences,
                    conflicting_factors=result.conflicting_factors,
                    needs_review=needs_review,
                )
                storage.mark_proposal_analyzed(row["id"])

                report.analyzed += 1
                if needs_review:
                    report.escalated += 1
                else:
                    report.auto_decided += 1

                if verbose:
                    flag = " [REVIEW NEEDED]" if needs_review else ""
                    print(
                        f"  {row['ticker']:8s} #{row['proposal_number']:<4} "
                        f"{result.recommendation:8s} conf={result.confidence:.2f} "
                        f"imp={result.importance:.2f}{flag} — {row['title'][:50]}"
                    )

        return report

    # ------------------------------------------------------------------
    # Escalation logic
    # ------------------------------------------------------------------

    def _should_escalate(self, result: AnalysisResult) -> bool:
        """Return True if this decision should be handed off to the user."""
        ptype = result.proposal_type

        if ptype in {t.value for t in ALWAYS_ESCALATE_TYPES}:
            return True

        if (
            ptype == ProposalType.EXECUTIVE_COMPENSATION.value
            and result.importance >= EXEC_COMP_IMPORTANCE_ESCALATE
        ):
            return True

        if (
            result.confidence < ESCALATION_CONFIDENCE_THRESHOLD
            and result.importance > ESCALATION_IMPORTANCE_THRESHOLD
        ):
            return True

        return False

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    def get_review_queue(self) -> list:
        return storage.get_review_queue()

    def apply_user_vote(
        self,
        decision_id: int,
        vote: str,
        note: str = "",
        learn: bool = True,
    ) -> None:
        rows = storage.get_review_queue()
        matching = [r for r in rows if r["decision_id"] == decision_id]

        storage.apply_user_override(decision_id, vote, note)

        if learn and matching:
            row = matching[0]
            self.prefs.learn_from_vote(
                ticker=row["ticker"],
                proposal_title=row["title"],
                proposal_type=row["proposal_type"],
                user_vote=vote,
                ai_recommendation=row["recommendation"],
                analyzer=self.analyzer,
            )
