from __future__ import annotations

from dataclasses import dataclass, field

from beartype import beartype
from beartype.typing import Any

from .constants import (
    CATEGORY_OTHER,
    DECISION_REASON_ADVISORY_ONLY,
    DECISION_REASON_HARD_GATE_FAILURE,
    DECISION_REASON_RISK_ESCALATION,
    DEFAULT_TICKET_VERB,
    FINDING_TYPE_HARD_GATE,
    FINDING_TYPE_SOFT,
    GATE_STATUS_FAIL,
    GATE_STATUS_PASS,
    HARD_GATE_ALIASES,
    HARD_GATE_ID_PREFIX,
    HARD_GATES,
    MERGE_DECISION_BLOCK,
    REQUIRED_CI_GATE,
    MERGE_DECISION_NON_BLOCK,
    MERGE_STATUS_ALLOW,
    MERGE_STATUS_BLOCKED,
    P0_KEYWORDS,
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    REQUIRED_CI_GATE,
    RATIONALE_ELEVATED_TRACK,
    RATIONALE_HARD_GATE_BLOCKING,
    RATIONALE_LOW_ADVISORY,
    RATIONALE_REQUIRED_GATES_POLICY,
    RATIONALE_WEIGHTED_BLOCK,
    SOFT_CATEGORY_SET,
    SOFT_SCORE_THRESHOLD_BLOCK,
    SOFT_SCORE_THRESHOLD_ELEVATED_P2,
    TICKET_OWNER_CI_MAINTAINERS,
    TICKET_VERB_BY_CATEGORY,
)
from .contracts import AuditResult, DeepValidationResult, Finding, PipelineConfig, PostPushContext, Severity, StageStatus


@dataclass(frozen=True, slots=True)
class FindingScores:
    severity: int
    likelihood: int
    blast_radius: int
    detectability: int
    raw_score: int
    score: int

    def to_dict(self) -> dict[str, int]:
        return {
            "severity": self.severity,
            "likelihood": self.likelihood,
            "blast_radius": self.blast_radius,
            "detectability": self.detectability,
            "raw_score": self.raw_score,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class TicketPayload:
    required: bool
    title: str
    context: str
    acceptance_criteria: list[str]
    owner: str
    priority: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "title": self.title,
            "context": self.context,
            "acceptance_criteria": self.acceptance_criteria,
            "owner": self.owner,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class MergeDecisionHardGate:
    name: str
    status: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class MergeDecisionFinding:
    id: str
    title: str
    category: str
    finding_type: str
    scores: FindingScores
    decision: str
    priority: str
    rationale: str
    ticket: TicketPayload

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "type": self.finding_type,
            "scores": self.scores.to_dict(),
            "decision": self.decision,
            "priority": self.priority,
            "rationale": self.rationale,
            "ticket": self.ticket.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MergeDecisionSummary:
    blocking_count: int
    non_blocking_count: int
    max_soft_score: int
    required_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking_count": self.blocking_count,
            "non_blocking_count": self.non_blocking_count,
            "max_soft_score": self.max_soft_score,
            "required_actions": self.required_actions,
        }


@dataclass(frozen=True, slots=True)
class MergeDecisionResult:
    decision: str
    decision_reason: str
    priority: str
    merge_status: str
    hard_gates: list[MergeDecisionHardGate]
    findings: list[MergeDecisionFinding]
    summary: MergeDecisionSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "priority": self.priority,
            "merge_status": self.merge_status,
            "hard_gates": [gate.to_dict() for gate in self.hard_gates],
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": self.summary.to_dict(),
        }


# Severity -> score (1-5); kept here to avoid constants importing contracts (Severity enum)
_SEVERITY_S: dict[Severity, int] = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class RuleBasedMergeDecisionTriage:
    __slots__ = ()

    @beartype
    def decide(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> MergeDecisionResult:
        _ = (context, config)
        hard_gates = self._evaluate_hard_gates(validation)
        hard_gate_failures = [gate for gate in hard_gates if gate.status == GATE_STATUS_FAIL]

        findings: list[MergeDecisionFinding] = []
        for failed_gate in hard_gate_failures:
            findings.append(self._hard_gate_finding(failed_gate.name))

        max_soft_score = 0
        for finding in audit.new_findings:
            triaged_finding, soft_score = self._triage_finding(finding)
            findings.append(triaged_finding)
            max_soft_score = max(max_soft_score, soft_score)

        blocking_findings = [item for item in findings if item.decision == MERGE_DECISION_BLOCK]
        if hard_gate_failures:
            decision = MERGE_DECISION_BLOCK
            decision_reason = DECISION_REASON_HARD_GATE_FAILURE
            priority = PRIORITY_P0
            merge_status = MERGE_STATUS_BLOCKED
        elif blocking_findings:
            decision = MERGE_DECISION_BLOCK
            decision_reason = DECISION_REASON_RISK_ESCALATION
            priority = PRIORITY_P0 if any(item.priority == PRIORITY_P0 for item in blocking_findings) else PRIORITY_P1
            merge_status = MERGE_STATUS_BLOCKED
        else:
            decision = MERGE_DECISION_NON_BLOCK
            decision_reason = DECISION_REASON_ADVISORY_ONLY
            priority = PRIORITY_P2 if max_soft_score >= SOFT_SCORE_THRESHOLD_ELEVATED_P2 else PRIORITY_P3
            merge_status = MERGE_STATUS_ALLOW

        summary = self._build_summary(
            findings=findings,
            hard_gate_failures=hard_gate_failures,
            max_soft_score=max_soft_score,
        )
        return MergeDecisionResult(
            decision=decision,
            decision_reason=decision_reason,
            priority=priority,
            merge_status=merge_status,
            hard_gates=hard_gates,
            findings=findings,
            summary=summary,
        )

    def _evaluate_hard_gates(self, validation: DeepValidationResult) -> list[MergeDecisionHardGate]:
        failed_by_signal = set()
        for failure in validation.blocking_failures:
            normalized_failure = _normalize_text(failure)
            for gate_name, aliases in HARD_GATE_ALIASES.items():
                if any(alias in normalized_failure for alias in aliases):
                    failed_by_signal.add(gate_name)

        for check in validation.checks:
            gate_name = self._canonical_hard_gate(check.name)
            if gate_name and check.status != StageStatus.PASS:
                failed_by_signal.add(gate_name)

        if validation.blocking_failures and not failed_by_signal:
            failed_by_signal.add(REQUIRED_CI_GATE)

        return [
            MergeDecisionHardGate(
                name=gate_name,
                status=GATE_STATUS_FAIL if gate_name in failed_by_signal else GATE_STATUS_PASS,
                blocking=True,
            )
            for gate_name in HARD_GATES
        ]

    def _triage_finding(self, finding: Finding) -> tuple[MergeDecisionFinding, int]:
        title = finding.title.strip() or finding.finding_id
        category = self._normalize_category(finding.category, title=title, recommendation=finding.recommendation)
        soft_scores = self._scores_for_soft_finding(finding)
        soft_score = soft_scores.score

        if self._is_hard_gate_finding(finding):
            decision = MERGE_DECISION_BLOCK
            priority = PRIORITY_P0
            rationale = RATIONALE_HARD_GATE_BLOCKING
            score_for_summary = 0
            ticket = self._ticket_for_finding(
                finding=finding,
                category=category,
                decision=decision,
                priority=priority,
                is_hard_gate=True,
            )
            result = MergeDecisionFinding(
                id=finding.finding_id,
                title=title,
                category=category,
                finding_type=FINDING_TYPE_HARD_GATE,
                scores=soft_scores,
                decision=decision,
                priority=priority,
                rationale=rationale,
                ticket=ticket,
            )
            return result, score_for_summary

        decision = MERGE_DECISION_BLOCK if soft_score >= SOFT_SCORE_THRESHOLD_BLOCK else MERGE_DECISION_NON_BLOCK
        priority = self._priority_for_finding(decision=decision, score=soft_score, text=_finding_text(finding))
        if decision == MERGE_DECISION_BLOCK:
            rationale = RATIONALE_WEIGHTED_BLOCK.format(score=soft_score)
        elif soft_score >= SOFT_SCORE_THRESHOLD_ELEVATED_P2:
            rationale = RATIONALE_ELEVATED_TRACK.format(score=soft_score)
        else:
            rationale = RATIONALE_LOW_ADVISORY.format(score=soft_score)

        ticket = self._ticket_for_finding(
            finding=finding,
            category=category,
            decision=decision,
            priority=priority,
            is_hard_gate=False,
        )
        return (
            MergeDecisionFinding(
                id=finding.finding_id,
                title=title,
                category=category,
                finding_type=FINDING_TYPE_SOFT,
                scores=soft_scores,
                decision=decision,
                priority=priority,
                rationale=rationale,
                ticket=ticket,
            ),
            soft_score,
        )

    def _hard_gate_finding(self, gate_name: str) -> MergeDecisionFinding:
        title = f"{gate_name} hard gate is failing"
        scores = FindingScores(severity=5, likelihood=5, blast_radius=5, detectability=1, raw_score=134, score=100)
        ticket = TicketPayload(
            required=True,
            title=f"Restore {gate_name} checks to green",
            context=f"Hard gate `{gate_name}` failed during deep validation.",
            acceptance_criteria=[
                f"`{gate_name}` reports PASS in required CI checks.",
                "Post-push report contains no hard-gate failures.",
            ],
            owner=TICKET_OWNER_CI_MAINTAINERS,
            priority=PRIORITY_P0,
        )
        return MergeDecisionFinding(
            id=f"{HARD_GATE_ID_PREFIX}{gate_name}",
            title=title,
            category=CATEGORY_OTHER,
            finding_type=FINDING_TYPE_HARD_GATE,
            scores=scores,
            decision=MERGE_DECISION_BLOCK,
            priority=PRIORITY_P0,
            rationale=RATIONALE_REQUIRED_GATES_POLICY,
            ticket=ticket,
        )

    def _scores_for_soft_finding(self, finding: Finding) -> FindingScores:
        severity = _clamp_metric(finding.risk_severity, fallback=_SEVERITY_S.get(finding.severity, 0))
        likelihood_default = 0 if severity == 0 else 1
        likelihood = _clamp_metric(finding.risk_likelihood, fallback=likelihood_default)
        blast_radius = _clamp_metric(finding.risk_blast_radius, fallback=likelihood_default)
        detectability = _clamp_metric(finding.risk_detectability, fallback=likelihood_default)

        raw_score = 12 * severity + 8 * likelihood + 6 * blast_radius + 4 * detectability
        score = min(100, raw_score)
        return FindingScores(
            severity=severity,
            likelihood=likelihood,
            blast_radius=blast_radius,
            detectability=detectability,
            raw_score=raw_score,
            score=score,
        )

    def _priority_for_finding(self, decision: str, score: int, text: str) -> str:
        if decision == MERGE_DECISION_BLOCK:
            if any(token in _normalize_text(text) for token in P0_KEYWORDS):
                return PRIORITY_P0
            return PRIORITY_P1
        if score >= SOFT_SCORE_THRESHOLD_ELEVATED_P2:
            return PRIORITY_P2
        return PRIORITY_P3

    def _ticket_for_finding(
        self,
        finding: Finding,
        category: str,
        decision: str,
        priority: str,
        is_hard_gate: bool,
    ) -> TicketPayload:
        verb = "Restore" if is_hard_gate else TICKET_VERB_BY_CATEGORY.get(category, DEFAULT_TICKET_VERB)
        title = f"{verb} {finding.title.strip() or finding.finding_id}"
        evidence = ", ".join(finding.evidence[:3]) if finding.evidence else "not specified"
        context = f"What: {finding.recommendation}. Where: {evidence}."

        if is_hard_gate or decision == MERGE_DECISION_BLOCK:
            acceptance = [
                "Implement a fix that removes the blocking condition.",
                "Add or update automated checks that verify the fix.",
            ]
        else:
            acceptance = [
                "Implement and document the recommended improvement.",
                "Add a test or validation signal proving the change works.",
            ]

        owner = finding.owner or (f"{finding.agent}-team" if finding.agent else "platform-team")
        return TicketPayload(
            required=True,
            title=title,
            context=context,
            acceptance_criteria=acceptance,
            owner=owner,
            priority=priority,
        )

    def _normalize_category(self, category: str, title: str, recommendation: str) -> str:
        candidate = (category or "").strip().lower()
        if candidate in SOFT_CATEGORY_SET:
            return candidate

        text = _normalize_text(f"{title} {recommendation}")
        if any(token in text for token in ("architecture", "design pattern")):
            return "architecture"
        if any(token in text for token in ("optimiz", "efficiency", "complexity")):
            return "optimization"
        if "refactor" in text:
            return "refactor"
        if any(token in text for token in ("style", "naming", "format")):
            return "style"
        if any(token in text for token in ("latency", "throughput", "memory", "slow", "performance")):
            return "performance"
        if any(token in text for token in ("bug", "risk", "issue", "edge case", "failure", "exception")):
            return "potential-issue"
        return CATEGORY_OTHER

    def _is_hard_gate_finding(self, finding: Finding) -> bool:
        if finding.finding_type.lower() == FINDING_TYPE_HARD_GATE:
            return True

        text = _normalize_text(_finding_text(finding))
        mentions_gate = any(
            any(alias in text for alias in aliases)
            for aliases in HARD_GATE_ALIASES.values()
        )
        return mentions_gate and any(token in text for token in ("fail", "failing", "not green"))

    def _canonical_hard_gate(self, check_name: str) -> str | None:
        normalized = _normalize_text(check_name)
        for gate_name, aliases in HARD_GATE_ALIASES.items():
            if any(alias in normalized for alias in aliases):
                return gate_name
        return None

    def _build_summary(
        self,
        findings: list[MergeDecisionFinding],
        hard_gate_failures: list[MergeDecisionHardGate],
        max_soft_score: int,
    ) -> MergeDecisionSummary:
        actions: list[str] = []
        for gate in hard_gate_failures:
            actions.append(f"Restore hard gate `{gate.name}` to PASS.")
        for finding in findings:
            actions.append(f"Create/track ticket: {finding.ticket.title} ({finding.ticket.priority}).")

        blocking = sum(1 for finding in findings if finding.decision == MERGE_DECISION_BLOCK)
        non_blocking = sum(1 for finding in findings if finding.decision == MERGE_DECISION_NON_BLOCK)
        return MergeDecisionSummary(
            blocking_count=blocking,
            non_blocking_count=non_blocking,
            max_soft_score=max_soft_score,
            required_actions=_dedupe_preserve_order(actions),
        )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _finding_text(finding: Finding) -> str:
    return " ".join([finding.title, finding.recommendation, " ".join(finding.evidence)])


def _normalize_text(value: str) -> str:
    return value.lower().replace("_", "-")


def _clamp_metric(value: int | None, fallback: int) -> int:
    candidate = fallback if value is None else value
    if candidate < 0:
        return 0
    if candidate > 5:
        return 5
    return int(candidate)
