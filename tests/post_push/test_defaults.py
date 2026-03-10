"""Tests for guardrails.post_push.defaults: SafeNoopCleanup, PassValidation, NoopAuditAgent, BaselineSynthesis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails import (  # noqa: E402
    BaselineSynthesis,
    Finding,
    MutationMode,
    NoopAuditAgent,
    PipelineConfig,
    PostPushContext,
    SafeNoopCleanup,
    WorkItemIdentity,
)
from guardrails.post_push.contracts import (  # noqa: E402
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    Severity,
    StageStatus,
    SynthesisResult,
    ValidationCheck,
)
from guardrails.post_push.defaults import PassValidation  # noqa: E402


def _context() -> PostPushContext:
    return PostPushContext(
        task_id="TASK-001",
        repo="org/repo",
        branch="main",
        sha="abc",
        identity=WorkItemIdentity(group_id="g1", commit_sha="abc", run_id="r1"),
        profile="full",
    )


def _config(mutation_mode: MutationMode = MutationMode.OFF) -> PipelineConfig:
    return PipelineConfig(mutation_mode=mutation_mode)


class SafeNoopCleanupTests(unittest.TestCase):
    def test_returns_pass_no_cleaned_no_skipped(self) -> None:
        cleanup = SafeNoopCleanup()
        result = cleanup.cleanup(context=_context(), config=_config())
        self.assertIsInstance(result, CleanupResult)
        self.assertEqual(result.status, StageStatus.PASS)
        self.assertEqual(result.cleaned, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.policy_violations, [])


class PassValidationTests(unittest.TestCase):
    def test_returns_pass_with_three_checks(self) -> None:
        v = PassValidation()
        result = v.validate(context=_context(), config=_config())
        self.assertIsInstance(result, DeepValidationResult)
        self.assertEqual(result.status, StageStatus.PASS)
        self.assertEqual(len(result.checks), 3)
        names = [c.name for c in result.checks]
        self.assertIn("unit-tests", names)
        self.assertIn("integration-tests", names)
        self.assertIn("type-check", names)
        self.assertTrue(all(c.status == StageStatus.PASS for c in result.checks))
        self.assertEqual(result.blocking_failures, [])
        self.assertEqual(result.non_blocking_notes, [])

    def test_mutation_mode_from_config(self) -> None:
        v = PassValidation()
        result = v.validate(context=_context(), config=_config(MutationMode.SAMPLE))
        self.assertEqual(result.mutation_mode, MutationMode.SAMPLE)

    def test_mutation_mode_off_default(self) -> None:
        v = PassValidation()
        result = v.validate(context=_context(), config=_config())
        self.assertEqual(result.mutation_mode, MutationMode.OFF)


class NoopAuditAgentTests(unittest.TestCase):
    def test_name_is_reviewer(self) -> None:
        agent = NoopAuditAgent()
        self.assertEqual(agent.name, "reviewer")

    def test_run_returns_single_info_finding(self) -> None:
        agent = NoopAuditAgent()
        validation = DeepValidationResult(status=StageStatus.PASS, checks=[])
        findings = agent.run(
            context=_context(),
            validation=validation,
            config=_config(),
        )
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertIsInstance(f, Finding)
        self.assertEqual(f.agent, "reviewer")
        self.assertEqual(f.finding_id, "REV-001")
        self.assertEqual(f.severity, Severity.INFO)
        self.assertEqual(f.title, "No critical issues discovered")
        self.assertEqual(f.evidence, ["audit:empty"])
        self.assertEqual(f.recommendation, "No action required")
        self.assertEqual(f.dedupe_key, "reviewer:no-critical-issues")


class BaselineSynthesisTests(unittest.TestCase):
    def test_synthesis_includes_context_and_stages(self) -> None:
        syn = BaselineSynthesis()
        cleanup = CleanupResult(status=StageStatus.PASS)
        validation = DeepValidationResult(status=StageStatus.PASS, checks=[])
        audit = AuditResult(status=StageStatus.PASS, new_findings=[])
        result = syn.synthesize(
            context=_context(),
            cleanup=cleanup,
            validation=validation,
            audit=audit,
            config=_config(),
        )
        self.assertIsInstance(result, SynthesisResult)
        self.assertEqual(result.status, StageStatus.PASS)
        self.assertIn("org/repo", result.summary_markdown)
        self.assertIn("main", result.summary_markdown)
        self.assertIn("abc", result.summary_markdown)
        self.assertIn("full", result.summary_markdown)
        self.assertIn("cleanup", result.summary_markdown.lower())
        self.assertIn("validation", result.summary_markdown.lower())
        self.assertIn("audit", result.summary_markdown.lower())
        self.assertIn("Interfaces", result.summary_markdown)
        self.assertIn("Architecture", result.summary_markdown)
        self.assertIn("Test Posture", result.summary_markdown)
        self.assertEqual(result.risks, [])
        self.assertEqual(result.follow_up_tasks, [])
        self.assertIsNone(result.partial_reason)

    def test_synthesis_partial_when_validation_has_blocking_failures(self) -> None:
        syn = BaselineSynthesis()
        cleanup = CleanupResult(status=StageStatus.PASS)
        validation = DeepValidationResult(
            status=StageStatus.FAIL,
            blocking_failures=["integration failed"],
            checks=[
                ValidationCheck("integration", StageStatus.FAIL, blocking=True),
            ],
        )
        audit = AuditResult(status=StageStatus.PASS, new_findings=[])
        result = syn.synthesize(
            context=_context(),
            cleanup=cleanup,
            validation=validation,
            audit=audit,
            config=_config(),
        )
        self.assertEqual(result.status, StageStatus.PARTIAL)
        self.assertEqual(result.risks, ["integration failed"])
        self.assertIsNotNone(result.partial_reason)
        self.assertIn("Blocking failures", result.partial_reason or "")


if __name__ == "__main__":
    unittest.main()
