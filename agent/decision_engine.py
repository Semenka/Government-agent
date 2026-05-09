"""
Decision engine — orchestrates analysis, applies escalation rules,
and coordinates between the analyzer, preference engine, and storage.

Optimizations:
  - Parallel proposal analysis via ThreadPoolExecutor
  - Pre-computed preferences context (loaded once per batch)
  - Two-pass analyze-with-critique by default (single-pass available via flag)
  - Direct decision-id lookup for non-interactive vote application
    (Telegram callbacks, webhooks, automation)
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
from agent.analyzer import (
    ProposalAnalyzer,
    AnalysisResult,
    CritiqueResult,
    build_thematic_context,
)
from agent.preference_engine import PreferenceEngine
from data import storage


@dataclass
class ProcessingReport:
    total: int
    analyzed: int
    auto_decided: int
    escalated: int
    revised_by_critique: int = 0
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
        two_pass: bool = True,
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
            if two_pass:
                thematic = build_thematic_context(
                    ticker=row["ticker"],
                    proposal_type=row["proposal_type"] or "other",
                    proposal_id=row["id"],
                )
                critique = self.analyzer.analyze_with_critique(
                    ticker=row["ticker"],
                    company_name=row["company_name"],
                    proposal_number=row["proposal_number"],
                    title=row["title"],
                    full_text=row["full_text"] or "",
                    management_rec=row["management_rec"] or "",
                    meeting_date=row["meeting_date"] or "",
                    preferences_context=preferences_context,
                    thematic_context=thematic,
                )
                return row, critique
            single = self.analyzer.analyze(
                ticker=row["ticker"],
                company_name=row["company_name"],
                proposal_number=row["proposal_number"],
                title=row["title"],
                full_text=row["full_text"] or "",
                management_rec=row["management_rec"] or "",
                meeting_date=row["meeting_date"] or "",
                preferences_context=preferences_context,
            )
            return row, CritiqueResult(
                final=single, pass1=single, critique_text="", revised=False,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_analyze_one, row): row for row in proposals
            }

            for future in as_completed(futures):
                row = futures[future]
                try:
                    _, critique = future.result()
                except Exception as exc:
                    err = f"[{row['ticker']} #{row['proposal_number']}] {exc}"
                    report.errors.append(err)
                    if verbose:
                        print(f"  ERROR: {err}")
                    continue

                self._persist_decision(row["id"], critique)

                report.analyzed += 1
                if critique.revised:
                    report.revised_by_critique += 1
                if self._should_escalate(critique.final):
                    report.escalated += 1
                else:
                    report.auto_decided += 1

                if verbose:
                    flag = " [REVIEW NEEDED]" if self._should_escalate(critique.final) else ""
                    revised = " [REVISED BY CRITIQUE]" if critique.revised else ""
                    print(
                        f"  {row['ticker']:8s} #{row['proposal_number']:<4} "
                        f"{critique.final.recommendation:8s} "
                        f"conf={critique.final.confidence:.2f} "
                        f"imp={critique.final.importance:.2f}"
                        f"{flag}{revised} — {row['title'][:50]}"
                    )

        return report

    # ------------------------------------------------------------------
    # Single-proposal processing (used by meeting-aware fan-out)
    # ------------------------------------------------------------------

    def process_for_meeting(
        self,
        proposal_id: int,
        two_pass: bool = True,
        force: bool = False,
    ) -> int | None:
        """
        Ensure a decision exists for the given proposal. If a decision is
        already present and `force` is False, this is a no-op and returns
        the existing decision id. Otherwise runs the analyzer (with critique
        if `two_pass` is True) and persists the result.

        Used by scheduler.run_meeting_check to lazily analyze proposals as
        their meeting tier (T-14, T-7, T-3, T-1) approaches.
        """
        if not force:
            existing = storage.get_decision_for_proposal(proposal_id)
            if existing:
                return existing["id"]

        with storage.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            return None

        preferences_context = self.prefs.get_context()
        proposal_type = row["proposal_type"] or "other"

        if two_pass:
            thematic = build_thematic_context(
                ticker=row["ticker"],
                proposal_type=proposal_type,
                proposal_id=row["id"],
            )
            critique = self.analyzer.analyze_with_critique(
                ticker=row["ticker"],
                company_name=row["company_name"],
                proposal_number=row["proposal_number"],
                title=row["title"],
                full_text=row["full_text"] or "",
                management_rec=row["management_rec"] or "",
                meeting_date=row["meeting_date"] or "",
                preferences_context=preferences_context,
                thematic_context=thematic,
            )
        else:
            single = self.analyzer.analyze(
                ticker=row["ticker"],
                company_name=row["company_name"],
                proposal_number=row["proposal_number"],
                title=row["title"],
                full_text=row["full_text"] or "",
                management_rec=row["management_rec"] or "",
                meeting_date=row["meeting_date"] or "",
                preferences_context=preferences_context,
            )
            critique = CritiqueResult(
                final=single, pass1=single, critique_text="", revised=False,
            )

        return self._persist_decision(proposal_id, critique)

    # ------------------------------------------------------------------
    # Persistence helper (single source of truth for the decision row +
    # both analysis passes)
    # ------------------------------------------------------------------

    def _persist_decision(self, proposal_id: int, critique: CritiqueResult) -> int:
        final = critique.final
        needs_review = self._should_escalate(final)

        decision_id = storage.save_decision(
            proposal_id=proposal_id,
            recommendation=final.recommendation,
            confidence=final.confidence,
            importance=final.importance,
            reasoning=final.reasoning,
            governance_concerns=final.governance_concerns,
            aligned_preferences=final.aligned_preferences,
            conflicting_factors=final.conflicting_factors,
            needs_review=needs_review,
            pass1_recommendation=critique.pass1.recommendation,
            pass1_reasoning=critique.pass1.reasoning,
            critique_text=critique.critique_text,
            critique_revised=critique.revised,
        )
        storage.mark_proposal_analyzed(proposal_id)

        # Persist both passes for full traceability
        if decision_id:
            storage.save_analysis_pass(
                decision_id=decision_id,
                pass_number=1,
                recommendation=critique.pass1.recommendation,
                confidence=critique.pass1.confidence,
                reasoning=critique.pass1.reasoning,
                raw_json=critique.pass1.raw_json,
            )
            if critique.critique_text or critique.revised:
                storage.save_analysis_pass(
                    decision_id=decision_id,
                    pass_number=2,
                    recommendation=final.recommendation,
                    confidence=final.confidence,
                    reasoning=final.reasoning,
                    raw_json=final.raw_json,
                )
        return decision_id

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
    ) -> bool:
        """
        Record a user vote against a specific decision_id. Returns True if a
        matching decision was found and updated; False if the decision_id
        does not exist.

        Looks up the decision directly (no review-queue scan), so this is
        safe to call from non-interactive contexts: Telegram callbacks,
        webhooks, automation scripts.
        """
        row = storage.get_decision_with_proposal(decision_id)
        if row is None:
            return False

        storage.apply_user_override(decision_id, vote, note)

        if learn:
            self.prefs.learn_from_vote(
                ticker=row["ticker"],
                proposal_title=row["title"],
                proposal_type=row["proposal_type"],
                user_vote=vote,
                ai_recommendation=row["recommendation"],
                analyzer=self.analyzer,
                ai_reasoning=row["reasoning"] or "",
                user_note=note,
            )
        return True
