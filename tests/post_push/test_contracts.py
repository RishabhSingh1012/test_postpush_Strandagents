"""Tests for guardrails.post_push.contracts: enums, dataclasses, and to_dict serialization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.contracts import (  # noqa: E402
    AuditResult,
    CleanupResult,
    CleanupSkip,
    DeepValidationResult,
    ErrorEnvelope,
    Finding,
    MutationMode,
    PipelineConfig,
    PostPushContext,
    Severity,
    StageStatus,
    SynthesisResult,
    ValidationCheck,
    WorkItemIdentity,
)


class SeverityTests(unittest.TestCase):
    def test_severity_values(self) -> None:
        self.assertEqual(Severity.CRITICAL.value, "critical")
        self.assertEqual(Severity.HIGH.value, "high")
        self.assertEqual(Severity.MEDIUM.value, "medium")
        self.assertEqual(Severity.LOW.value, "low")
        self.assertEqual(Severity.INFO.value, "info")


class StageStatusTests(unittest.TestCase):
    def test_stage_status_values(self) -> None:
        self.assertEqual(StageStatus.PASS.value, "pass")
        self.assertEqual(StageStatus.FAIL.value, "fail")
        self.assertEqual(StageStatus.PARTIAL.value, "partial")
        self.assertEqual(StageStatus.SKIP.value, "skip")


class MutationModeTests(unittest.TestCase):
    def test_mutation_mode_values(self) -> None:
        self.assertEqual(MutationMode.OFF.value, "off")
        self.assertEqual(MutationMode.SAMPLE.value, "sample")
        self.assertEqual(MutationMode.FULL.value, "full")


class WorkItemIdentityTests(unittest.TestCase):
    def test_immutable_and_attributes(self) -> None:
        identity = WorkItemIdentity(group_id="g1", commit_sha="abc", run_id="r1")
        self.assertEqual(identity.group_id, "g1")
        self.assertEqual(identity.commit_sha, "abc")
        self.assertEqual(identity.run_id, "r1")


class PostPushContextTests(unittest.TestCase):
    def test_required_and_optional_fields(self) -> None:
        identity = WorkItemIdentity(group_id="g1", commit_sha="sha", run_id="run1")
        ctx = PostPushContext(
            task_id="TASK-001",
            repo="org/repo",
            branch="main",
            sha="abc123",
            identity=identity,
            profile="full",
            pr="42",
            changed_files=["a.py", "b.py"],
            runtime_id="RT-1",
            metadata={"key": "value"},
        )
        self.assertEqual(ctx.task_id, "TASK-001")
        self.assertEqual(ctx.repo, "org/repo")
        self.assertEqual(ctx.branch, "main")
        self.assertEqual(ctx.sha, "abc123")
        self.assertEqual(ctx.identity, identity)
        self.assertEqual(ctx.profile, "full")
        self.assertEqual(ctx.pr, "42")
        self.assertEqual(ctx.changed_files, ["a.py", "b.py"])
        self.assertEqual(ctx.runtime_id, "RT-1")
        self.assertEqual(ctx.metadata, {"key": "value"})

    def test_default_profile_and_none_pr(self) -> None:
        identity = WorkItemIdentity(group_id="g1", commit_sha="sha", run_id="r1")
        ctx = PostPushContext(
            task_id="T1",
            repo="r",
            branch="b",
            sha="s",
            identity=identity,
        )
        self.assertEqual(ctx.profile, "light")
        self.assertIsNone(ctx.pr)
        self.assertEqual(ctx.changed_files, [])
        self.assertEqual(ctx.metadata, {})


class PipelineConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.cleanup_mode, "safe")
        self.assertEqual(config.mutation_mode, MutationMode.OFF)
        self.assertTrue(config.run_audits_on_validation_failure)
        self.assertEqual(config.artifacts_root, "artifacts")

    def test_custom_values(self) -> None:
        config = PipelineConfig(
            cleanup_mode="aggressive",
            mutation_mode=MutationMode.SAMPLE,
            run_audits_on_validation_failure=False,
            artifacts_root="out",
        )
        self.assertEqual(config.cleanup_mode, "aggressive")
        self.assertEqual(config.mutation_mode, MutationMode.SAMPLE)
        self.assertFalse(config.run_audits_on_validation_failure)
        self.assertEqual(config.artifacts_root, "out")


class CleanupSkipTests(unittest.TestCase):
    def test_fields(self) -> None:
        skip = CleanupSkip(item="worktree", reason="in use")
        self.assertEqual(skip.item, "worktree")
        self.assertEqual(skip.reason, "in use")


class CleanupResultTests(unittest.TestCase):
    def test_to_dict_full(self) -> None:
        skip = CleanupSkip(item="x", reason="y")
        result = CleanupResult(
            status=StageStatus.PARTIAL,
            cleaned=["a", "b"],
            skipped=[skip],
            policy_violations=["violation"],
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "partial")
        self.assertEqual(d["cleaned"], ["a", "b"])
        self.assertEqual(len(d["skipped"]), 1)
        self.assertEqual(d["skipped"][0]["item"], "x")
        self.assertEqual(d["skipped"][0]["reason"], "y")
        self.assertEqual(d["policy_violations"], ["violation"])

    def test_to_dict_empty(self) -> None:
        result = CleanupResult(status=StageStatus.PASS)
        d = result.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertEqual(d["cleaned"], [])
        self.assertEqual(d["skipped"], [])
        self.assertEqual(d["policy_violations"], [])


class ValidationCheckTests(unittest.TestCase):
    def test_to_dict_with_details(self) -> None:
        check = ValidationCheck(
            name="unit",
            status=StageStatus.FAIL,
            blocking=True,
            details="3 failed",
        )
        d = check.to_dict()
        self.assertEqual(d["name"], "unit")
        self.assertEqual(d["status"], "fail")
        self.assertTrue(d["blocking"])
        self.assertEqual(d["details"], "3 failed")

    def test_to_dict_default_details(self) -> None:
        check = ValidationCheck(name="lint", status=StageStatus.PASS, blocking=False)
        self.assertEqual(check.to_dict()["details"], "")


class DeepValidationResultTests(unittest.TestCase):
    def test_to_dict_full(self) -> None:
        check = ValidationCheck(name="c1", status=StageStatus.PASS, blocking=True)
        result = DeepValidationResult(
            status=StageStatus.PASS,
            checks=[check],
            blocking_failures=[],
            non_blocking_notes=["note"],
            mutation_mode=MutationMode.SAMPLE,
            mutation_score=80,
            mutation_threshold=70,
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertEqual(len(d["checks"]), 1)
        self.assertEqual(d["checks"][0]["name"], "c1")
        self.assertEqual(d["blocking_failures"], [])
        self.assertEqual(d["non_blocking_notes"], ["note"])
        self.assertEqual(d["mutation"]["mode"], "sample")
        self.assertEqual(d["mutation"]["score"], 80)
        self.assertEqual(d["mutation"]["threshold"], 70)

    def test_to_dict_mutation_none(self) -> None:
        result = DeepValidationResult(status=StageStatus.FAIL, blocking_failures=["err"])
        d = result.to_dict()
        self.assertIsNone(d["mutation"]["score"])
        self.assertIsNone(d["mutation"]["threshold"])


class FindingTests(unittest.TestCase):
    def test_to_dict(self) -> None:
        finding = Finding(
            agent="reviewer",
            finding_id="F-001",
            severity=Severity.HIGH,
            title="Title",
            evidence=["e1", "e2"],
            recommendation="Fix it",
            dedupe_key="key-1",
        )
        d = finding.to_dict()
        self.assertEqual(d["agent"], "reviewer")
        self.assertEqual(d["finding_id"], "F-001")
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["title"], "Title")
        self.assertEqual(d["evidence"], ["e1", "e2"])
        self.assertEqual(d["recommendation"], "Fix it")
        self.assertEqual(d["dedupe_key"], "key-1")


class AuditResultTests(unittest.TestCase):
    def test_to_dict_with_findings(self) -> None:
        finding = Finding(
            agent="a",
            finding_id="f1",
            severity=Severity.LOW,
            title="t",
            evidence=[],
            recommendation="r",
            dedupe_key="k",
        )
        result = AuditResult(
            status=StageStatus.PASS,
            new_findings=[finding],
            unresolved_rollups=[{"finding_id": "f2", "title": "T2"}],
            known_issue_refs=[{"finding_id": "f3", "dedupe_key": "k3", "issue_ref": "REF-1"}],
            errors=[],
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertEqual(len(d["new_findings"]), 1)
        self.assertEqual(d["new_findings"][0]["finding_id"], "f1")
        self.assertEqual(d["unresolved_rollups"], [{"finding_id": "f2", "title": "T2"}])
        self.assertEqual(len(d["known_issue_refs"]), 1)
        self.assertEqual(d["errors"], [])

    def test_to_dict_with_errors(self) -> None:
        result = AuditResult(status=StageStatus.FAIL, errors=["err1"])
        d = result.to_dict()
        self.assertEqual(d["errors"], ["err1"])


class SynthesisResultTests(unittest.TestCase):
    def test_to_dict_full(self) -> None:
        result = SynthesisResult(
            status=StageStatus.PARTIAL,
            summary_markdown="# Summary",
            risks=["r1"],
            follow_up_tasks=["task1"],
            partial_reason="reason",
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "partial")
        self.assertEqual(d["risks"], ["r1"])
        self.assertEqual(d["follow_up_tasks"], ["task1"])
        self.assertEqual(d["partial_reason"], "reason")

    def test_to_dict_minimal(self) -> None:
        result = SynthesisResult(status=StageStatus.PASS, summary_markdown="")
        d = result.to_dict()
        self.assertIsNone(d.get("partial_reason"))


class ErrorEnvelopeTests(unittest.TestCase):
    def test_to_dict(self) -> None:
        envelope = ErrorEnvelope(
            error=True,
            stage="post-push",
            step="cleanup",
            code="CLEANUP_FAILED",
            message="msg",
            task_id="TASK-001",
            details={"key": "value"},
        )
        d = envelope.to_dict()
        self.assertTrue(d["error"])
        self.assertEqual(d["stage"], "post-push")
        self.assertEqual(d["step"], "cleanup")
        self.assertEqual(d["code"], "CLEANUP_FAILED")
        self.assertEqual(d["message"], "msg")
        self.assertEqual(d["task_id"], "TASK-001")
        self.assertEqual(d["details"], {"key": "value"})


if __name__ == "__main__":
    unittest.main()
