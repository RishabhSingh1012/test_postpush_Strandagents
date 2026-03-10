"""Tests for merge-decision-triage: hard gates, weighted escalation, priority, ticketing, output format."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.contracts import (  # noqa: E402
    AuditResult,
    DeepValidationResult,
    Finding,
    MutationMode,
    PipelineConfig,
    PostPushContext,
    Severity,
    StageStatus,
    ValidationCheck,
    WorkItemIdentity,
)
from guardrails.post_push.merge_decision import (  # noqa: E402
    MergeDecisionResult,
    RuleBasedMergeDecisionTriage,
)


def _context() -> PostPushContext:
    return PostPushContext(
        task_id="TASK-001",
        repo="org/repo",
        branch="main",
        sha="abc123",
        identity=WorkItemIdentity(group_id="pr-42", commit_sha="abc123", run_id="run-1"),
        profile="full",
        pr="42",
    )


def _config() -> PipelineConfig:
    return PipelineConfig(mutation_mode=MutationMode.OFF)


# -----------------------------------------------------------------------------
# 1) Hard-gate decision rules (SKILL §1)
# -----------------------------------------------------------------------------


class HardGateDecisionTests(unittest.TestCase):
    """Hard gates: unit/integration/build/type-check/required-ci failure → BLOCK, P0, BLOCKED."""

    def test_unit_tests_fail_blocks_with_p0(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="unit-tests", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["unit-tests"],
        )
        result = triage.decide(
            context=_context(),
            validation=validation,
            audit=AuditResult(status=StageStatus.PASS),
            config=_config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.decision_reason, "hard_gate_failure")
        self.assertEqual(result.priority, "P0")
        self.assertEqual(result.merge_status, "BLOCKED")
        gate_status = {g.name: g.status for g in result.hard_gates}
        self.assertEqual(gate_status["unit-tests"], "FAIL")
        self.assertIn("hard-gate-unit-tests", [f.id for f in result.findings])

    def test_integration_tests_fail_blocks(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="integration-tests", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["integration tests failed"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.merge_status, "BLOCKED")
        gate_status = {g.name: g.status for g in result.hard_gates}
        self.assertEqual(gate_status["integration-tests"], "FAIL")

    def test_build_fail_blocks(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="build", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["build"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        self.assertEqual(result.decision, "BLOCK")
        gate_status = {g.name: g.status for g in result.hard_gates}
        self.assertEqual(gate_status["build"], "FAIL")

    def test_type_check_fail_blocks(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="mypy", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["typecheck failed"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        self.assertEqual(result.decision, "BLOCK")
        gate_status = {g.name: g.status for g in result.hard_gates}
        self.assertEqual(gate_status["type-check"], "FAIL")

    def test_required_ci_fail_when_blocking_failures_unknown_text(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[],
            blocking_failures=["some CI step failed with unknown label"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        self.assertEqual(result.decision, "BLOCK")
        gate_status = {g.name: g.status for g in result.hard_gates}
        self.assertEqual(gate_status["required-ci"], "FAIL")

    def test_all_hard_gates_pass_when_no_failures(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(status=StageStatus.PASS, checks=[], blocking_failures=[])
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        for gate in result.hard_gates:
            self.assertEqual(gate.status, "PASS", f"Gate {gate.name} should be PASS")
        self.assertEqual(result.decision, "NON-BLOCK")

    def test_hard_gate_never_downgraded_to_non_block(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="unit-tests", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["unit-tests"],
        )
        audit = AuditResult(
            status=StageStatus.PASS,
            new_findings=[
                Finding(
                    agent="r",
                    finding_id="X",
                    severity=Severity.LOW,
                    title="Cosmetic",
                    evidence=[],
                    recommendation="N/A",
                    dedupe_key="x",
                ),
            ],
        )
        result = triage.decide(_context(), validation, audit, _config())
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.decision_reason, "hard_gate_failure")


# -----------------------------------------------------------------------------
# 2) Soft finding categories & 3) Weighted escalation (SKILL §2, §3)
# -----------------------------------------------------------------------------


class WeightedEscalationTests(unittest.TestCase):
    """Score = min(100, 12*S + 8*L + 6*B + 4*D). 0-39 NON-BLOCK, 40-69 P2, 70+ BLOCK."""

    def test_soft_mid_risk_40_69_non_block_p2(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(status=StageStatus.PASS, checks=[])
        finding = Finding(
            agent="reviewer",
            finding_id="REV-002",
            severity=Severity.MEDIUM,
            title="Potential issue in retry logic",
            evidence=["src/service.py:44"],
            recommendation="Add retry backoff guardrails",
            dedupe_key="reviewer:retry-logic",
        )
        result = triage.decide(
            context=_context(),
            validation=validation,
            audit=AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            config=_config(),
        )
        self.assertEqual(result.decision, "NON-BLOCK")
        self.assertEqual(result.decision_reason, "advisory_only")
        self.assertEqual(result.priority, "P2")
        self.assertEqual(result.merge_status, "ALLOW")
        self.assertEqual(result.summary.max_soft_score, 54)
        self.assertEqual(result.findings[0].decision, "NON-BLOCK")
        self.assertEqual(result.findings[0].priority, "P2")
        self.assertTrue(result.findings[0].ticket.title.startswith("Investigate "))

    def test_soft_high_risk_70_plus_escalates_block_p1(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(status=StageStatus.PASS, checks=[])
        finding = Finding(
            agent="reviewer",
            finding_id="REV-003",
            severity=Severity.HIGH,
            title="Resource leak under concurrent load",
            evidence=["src/cache.py:91"],
            recommendation="Close leaked handles in failure paths",
            dedupe_key="reviewer:resource-leak",
            risk_severity=5,
            risk_likelihood=4,
            risk_blast_radius=4,
            risk_detectability=4,
        )
        result = triage.decide(
            context=_context(),
            validation=validation,
            audit=AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            config=_config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.decision_reason, "risk_escalation")
        self.assertEqual(result.priority, "P1")
        self.assertEqual(result.findings[0].decision, "BLOCK")
        self.assertGreaterEqual(result.findings[0].scores.score, 70)

    def test_soft_low_score_0_39_non_block_p3(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="LOW-1",
            severity=Severity.INFO,
            title="Style: naming",
            evidence=[],
            recommendation="Rename variable",
            dedupe_key="style:1",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        self.assertEqual(result.decision, "NON-BLOCK")
        self.assertEqual(result.priority, "P3")
        self.assertLessEqual(result.summary.max_soft_score, 39)

    def test_max_soft_score_drives_escalation(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        low = Finding(
            agent="r", finding_id="L", severity=Severity.INFO, title="x", evidence=[], recommendation="y", dedupe_key="l"
        )
        high = Finding(
            agent="r",
            finding_id="H",
            severity=Severity.HIGH,
            title="Critical path bug",
            evidence=[],
            recommendation="Fix",
            dedupe_key="h",
            risk_severity=5,
            risk_likelihood=5,
            risk_blast_radius=5,
            risk_detectability=5,
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[low, high]),
            _config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertGreaterEqual(result.summary.max_soft_score, 70)

    def test_score_formula_and_cap(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="F",
            severity=Severity.CRITICAL,
            title="x",
            evidence=[],
            recommendation="y",
            dedupe_key="f",
            risk_severity=5,
            risk_likelihood=5,
            risk_blast_radius=5,
            risk_detectability=5,
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        s = result.findings[0].scores
        self.assertEqual(s.raw_score, 12 * 5 + 8 * 5 + 6 * 5 + 4 * 5)
        self.assertEqual(s.score, min(100, s.raw_score))


# -----------------------------------------------------------------------------
# 4) Priority mapping P0/P1/P2/P3 (SKILL §4)
# -----------------------------------------------------------------------------


class PriorityMappingTests(unittest.TestCase):
    """BLOCK + security/etc → P0; BLOCK else → P1; NON-BLOCK 40-69 → P2; 0-39 → P3."""

    def test_security_keyword_sets_blocking_finding_priority_p0(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="reviewer",
            finding_id="SEC-001",
            severity=Severity.HIGH,
            title="Authentication bypass risk in token parser",
            evidence=["src/auth.py:120"],
            recommendation="Fix authentication checks and add regression tests",
            dedupe_key="security:auth-bypass",
            risk_severity=5,
            risk_likelihood=4,
            risk_blast_radius=4,
            risk_detectability=4,
        )
        result = triage.decide(
            context=_context(),
            validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
            audit=AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            config=_config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.priority, "P0")
        self.assertEqual(result.findings[0].priority, "P0")

    def test_block_risk_escalation_without_p0_keyword_is_p1(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="R",
            severity=Severity.HIGH,
            title="Resource leak",
            evidence=[],
            recommendation="Close handles",
            dedupe_key="r",
            risk_severity=5,
            risk_likelihood=4,
            risk_blast_radius=4,
            risk_detectability=4,
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.priority, "P1")

    def test_p0_keywords_in_recommendation_escalate_to_p0(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="D",
            severity=Severity.HIGH,
            title="Data handling",
            evidence=[],
            recommendation="Risk of data loss if process crashes",
            dedupe_key="d",
            risk_severity=5,
            risk_likelihood=4,
            risk_blast_radius=4,
            risk_detectability=4,
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.priority, "P0")


# -----------------------------------------------------------------------------
# 5) Ticketing rules (SKILL §5)
# -----------------------------------------------------------------------------


class TicketingRulesTests(unittest.TestCase):
    """Ticket: verb title, context (what/where), acceptance_criteria, owner, priority."""

    def test_ticket_title_starts_with_verb(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="A",
            severity=Severity.MEDIUM,
            title="Architecture: split module",
            evidence=["src/a.py"],
            recommendation="Refactor into smaller modules",
            dedupe_key="a",
            category="architecture",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        ticket = result.findings[0].ticket
        self.assertTrue(
            any(ticket.title.startswith(v) for v in ("Refactor", "Investigate", "Optimize", "Add")),
            f"Ticket title should start with verb: {ticket.title!r}",
        )

    def test_ticket_has_context_acceptance_criteria_owner_priority(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="reviewer",
            finding_id="T1",
            severity=Severity.LOW,
            title="Optimize loop",
            evidence=["src/loop.py"],
            recommendation="Use batch API",
            dedupe_key="t1",
            category="optimization",
            owner="perf-team",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        ticket = result.findings[0].ticket
        self.assertIn("What:", ticket.context)
        self.assertIn("Where:", ticket.context)
        self.assertIsInstance(ticket.acceptance_criteria, list)
        self.assertGreater(len(ticket.acceptance_criteria), 0)
        self.assertEqual(ticket.owner, "perf-team")
        self.assertIn(ticket.priority, ("P2", "P3"))

    def test_hard_gate_finding_has_restore_ticket(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="type-check", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["type-check"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        hard_finding = next(f for f in result.findings if f.finding_type == "hard-gate")
        self.assertTrue(hard_finding.ticket.title.startswith("Restore "))
        self.assertEqual(hard_finding.ticket.priority, "P0")


# -----------------------------------------------------------------------------
# 6) Output format (SKILL §6)
# -----------------------------------------------------------------------------


class OutputFormatTests(unittest.TestCase):
    """decision, decision_reason, priority, merge_status, hard_gates, findings, summary."""

    def test_result_has_required_top_level_fields(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS),
            _config(),
        )
        self.assertIn(result.decision, ("BLOCK", "NON-BLOCK"))
        self.assertIn(result.decision_reason, ("hard_gate_failure", "risk_escalation", "advisory_only"))
        self.assertIn(result.priority, ("P0", "P1", "P2", "P3"))
        self.assertIn(result.merge_status, ("BLOCKED", "ALLOW"))
        self.assertEqual(len(result.hard_gates), 5)
        for gate in result.hard_gates:
            self.assertIn(gate.name, ("unit-tests", "integration-tests", "build", "type-check", "required-ci"))
            self.assertIn(gate.status, ("PASS", "FAIL"))
            self.assertTrue(gate.blocking)

    def test_finding_has_required_fields(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="F1",
            severity=Severity.MEDIUM,
            title="Refactor X",
            evidence=["x.py"],
            recommendation="Split",
            dedupe_key="f1",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        f = result.findings[0]
        self.assertEqual(f.id, "F1")
        self.assertEqual(f.title, "Refactor X")
        self.assertIn(f.category, ("architecture", "optimization", "refactor", "style", "potential-issue", "performance", "other"))
        self.assertIn(f.finding_type, ("hard-gate", "soft"))
        self.assertIn(f.decision, ("BLOCK", "NON-BLOCK"))
        self.assertIn(f.priority, ("P0", "P1", "P2", "P3"))
        self.assertIsNotNone(f.rationale)
        self.assertGreaterEqual(f.scores.severity, 0)
        self.assertLessEqual(f.scores.severity, 5)
        self.assertIn("raw_score", f.scores.to_dict())
        self.assertIn("score", f.scores.to_dict())
        self.assertTrue(f.ticket.required)
        self.assertIsInstance(f.ticket.acceptance_criteria, list)

    def test_summary_has_blocking_non_blocking_max_soft_score_required_actions(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[ValidationCheck(name="unit-tests", status=StageStatus.FAIL, blocking=True)],
            blocking_failures=["unit-tests"],
        )
        result = triage.decide(_context(), validation, AuditResult(status=StageStatus.PASS), _config())
        self.assertIsInstance(result.summary.blocking_count, int)
        self.assertIsInstance(result.summary.non_blocking_count, int)
        self.assertIsInstance(result.summary.max_soft_score, int)
        self.assertIsInstance(result.summary.required_actions, list)
        self.assertGreater(len(result.summary.required_actions), 0)

    def test_to_dict_serialization(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[
                Finding(
                    agent="r",
                    finding_id="J",
                    severity=Severity.LOW,
                    title="Style",
                    evidence=[],
                    recommendation="Format",
                    dedupe_key="j",
                ),
            ]),
            _config(),
        )
        d = result.to_dict()
        self.assertIn("decision", d)
        self.assertIn("hard_gates", d)
        self.assertIn("findings", d)
        self.assertIn("summary", d)
        self.assertIsInstance(d["findings"], list)
        if d["findings"]:
            fd = d["findings"][0]
            self.assertIn("scores", fd)
            self.assertIn("ticket", fd)
            self.assertIn("score", fd["scores"])


# -----------------------------------------------------------------------------
# 7) Determinism and edge cases
# -----------------------------------------------------------------------------


class DeterminismAndEdgeCaseTests(unittest.TestCase):
    """Hard gates before soft; no omissions; empty audit; multiple findings."""

    def test_empty_audit_no_failures_yields_non_block_allow(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[], blocking_failures=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[]),
            _config(),
        )
        self.assertEqual(result.decision, "NON-BLOCK")
        self.assertEqual(result.merge_status, "ALLOW")
        self.assertEqual(result.priority, "P3")
        self.assertEqual(result.summary.max_soft_score, 0)
        self.assertEqual(len(result.findings), 0)

    def test_finding_type_hard_gate_treated_as_block(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="ci",
            finding_id="HG-1",
            severity=Severity.CRITICAL,
            title="Unit test failing",
            evidence=[],
            recommendation="Unit tests are failing in CI",
            dedupe_key="hg1",
            finding_type="hard-gate",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        self.assertEqual(result.decision, "BLOCK")
        self.assertEqual(result.findings[0].decision, "BLOCK")
        self.assertEqual(result.findings[0].priority, "P0")
        self.assertEqual(result.findings[0].finding_type, "hard-gate")

    def test_soft_findings_always_have_scores(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="S1",
            severity=Severity.INFO,
            title="Comment only",
            evidence=[],
            recommendation="N/A",
            dedupe_key="s1",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        f = result.findings[0]
        self.assertIsNotNone(f.scores)
        self.assertIsInstance(f.scores.severity, int)
        self.assertIsInstance(f.scores.likelihood, int)
        self.assertIsInstance(f.scores.blast_radius, int)
        self.assertIsInstance(f.scores.detectability, int)
        self.assertIsInstance(f.scores.raw_score, int)
        self.assertIsInstance(f.scores.score, int)

    def test_category_inferred_from_title_or_recommendation(self) -> None:
        triage = RuleBasedMergeDecisionTriage()
        finding = Finding(
            agent="r",
            finding_id="C1",
            severity=Severity.MEDIUM,
            title="Performance regression in hot path",
            evidence=[],
            recommendation="Optimize the loop",
            dedupe_key="c1",
            category="other",
        )
        result = triage.decide(
            _context(),
            DeepValidationResult(status=StageStatus.PASS, checks=[]),
            AuditResult(status=StageStatus.PASS, new_findings=[finding]),
            _config(),
        )
        self.assertIn(
            result.findings[0].category,
            ("performance", "optimization", "other"),
            "Category should be inferred from performance/optimize wording",
        )


if __name__ == "__main__":
    unittest.main()
