from __future__ import annotations

import re
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails import (  # noqa: E402
    BaselineSynthesis,
    FileArtifactStore,
    Finding,
    InMemoryDedupeStore,
    MutationMode,
    NoopAuditAgent,
    PipelineConfig,
    PostPushContext,
    PostPushPipeline,
    SafeNoopCleanup,
    Severity,
    StageStatus,
    ValidationCheck,
    WorkItemIdentity,
)
from guardrails.post_push.contracts import (  # noqa: E402
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    SynthesisResult,
)


class FailingValidation:
    def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        _ = (context, config)
        return DeepValidationResult(
            status=StageStatus.FAIL,
            checks=[
                ValidationCheck(
                    name="integration-tests",
                    status=StageStatus.FAIL,
                    blocking=True,
                    details="integration failure",
                )
            ],
            blocking_failures=["integration-tests"],
            mutation_mode=MutationMode.OFF,
        )


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.artifacts_dir = self.repo_root / "artifacts"
        if self.artifacts_dir.exists():
            shutil.rmtree(self.artifacts_dir)

    def tearDown(self) -> None:
        if self.artifacts_dir.exists():
            shutil.rmtree(self.artifacts_dir)

    def _context(self, run_id: str) -> PostPushContext:
        return PostPushContext(
            task_id="TASK-001",
            repo="org/repo",
            branch="agentic/TASK-001-demo",
            sha="abc1234",
            pr="482",
            profile="full",
            identity=WorkItemIdentity(group_id="pr-482", commit_sha="abc1234", run_id=run_id),
        )

    def _context_with_repo(self, run_id: str, profile: str = "full") -> PostPushContext:
        return PostPushContext(
            task_id="TASK-001",
            repo=str(self.repo_root),
            branch="agentic/TASK-001-demo",
            sha="abc1234",
            pr="482",
            profile=profile,
            identity=WorkItemIdentity(group_id="pr-482", commit_sha="abc1234", run_id=run_id),
        )

    def test_pipeline_pass_creates_index_report(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )

        outcome = pipeline.run(
            context=self._context(run_id="run-pass"),
            config=PipelineConfig(mutation_mode=MutationMode.SAMPLE),
        )

        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.report["overall_result"], "pass")
        self.assertIn("merge_decision", outcome.report)
        self.assertEqual(outcome.report["merge_decision"]["decision"], "NON-BLOCK")
        self.assertIn("adversarial", outcome.report["agents"])
        self.assertIn("reviewer", outcome.report["agents"])
        self.assertIn("path", outcome.report["synthesis"])
        self.assertTrue(Path(outcome.report["artifacts"]["post_push_report"]).exists())
        self.assertTrue(Path(outcome.report["artifacts"]["current_state_summary"]).exists())

    def test_pipeline_fails_on_blocking_validation(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=FailingValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )

        outcome = pipeline.run(
            context=self._context(run_id="run-fail"),
            config=PipelineConfig(mutation_mode=MutationMode.OFF),
        )

        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["overall_result"], "fail")
        self.assertIn("integration-tests", outcome.report["deep_validation"]["blocking_failures"])

    def test_pipeline_without_dedupe_store_still_runs(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=None,
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-no-dedupe"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertGreaterEqual(outcome.report["agents"]["new_findings"], 1)

    def test_run_audits_on_validation_failure_false_skips_audits(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=FailingValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-skip-audit"),
            config=PipelineConfig(run_audits_on_validation_failure=False),
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["deep_validation"]["status"], "fail")
        self.assertEqual(outcome.report["synthesis"]["status"], "partial")
        self.assertEqual(outcome.report["agents"]["new_findings"], 0)

    def test_report_rerun_suggestion_includes_task_profile_sha(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-rerun"),
            config=PipelineConfig(),
        )
        rerun = outcome.report["rerun"]
        self.assertIn("guardrails post-push", rerun)
        self.assertIn("--task-id TASK-001", rerun)
        self.assertIn("--profile full", rerun)
        self.assertIn("--sha abc1234", rerun)
        self.assertIn("--pr 482", rerun)

    def test_report_rerun_suggestion_without_pr(self) -> None:
        ctx = PostPushContext(
            task_id="TASK-001",
            repo="org/repo",
            branch="main",
            sha="sha1",
            identity=WorkItemIdentity(group_id="g1", commit_sha="sha1", run_id="r1"),
            profile="light",
            pr=None,
        )
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(context=ctx, config=PipelineConfig(artifacts_root="artifacts"))
        # Rerun must not contain a --pr <value> argument (--profile contains substring "--pr")
        self.assertIsNone(re.search(r" --pr \S+", outcome.report["rerun"]))

    def test_follow_up_tasks_include_validation_blocking_and_synthesis(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=FailingValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-followup"),
            config=PipelineConfig(run_audits_on_validation_failure=True),
        )
        next_actions = outcome.report["next_actions"]
        self.assertTrue(
            any("integration-tests" in a or "Resolve blocking" in a for a in next_actions),
            msg=f"next_actions should mention blocking: {next_actions}",
        )

    def test_dedupe_known_issue_ref_excluded_from_new_findings(self) -> None:
        known = {"reviewer:no-critical-issues": "REF-KNOWN-1"}
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(known_issues=known),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-known"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.report["agents"]["new_findings"], 0)
        self.assertIn("audit_findings", outcome.report["artifacts"])

    def test_dedupe_rollup_when_finding_key_already_open(self) -> None:
        dedupe = InMemoryDedupeStore()
        dedupe.save_open_keys("pr-482", {"reviewer:no-critical-issues"})
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=dedupe,
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-rollup"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.report["agents"]["rollups"], 1)
        self.assertEqual(outcome.report["agents"]["new_findings"], 0)

    def test_cleanup_exception_records_fail_in_report(self) -> None:
        class RaisingCleanup:
            def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
                raise RuntimeError("cleanup failed")

        pipeline = PostPushPipeline(
            cleanup=RaisingCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-cleanup-fail"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["overall_result"], "fail")
        self.assertEqual(outcome.report["cleanup"]["status"], "fail")
        self.assertEqual(len(outcome.report["cleanup"]["policy_violations"]), 1)
        self.assertIn("CLEANUP_FAILED", outcome.report["cleanup"]["policy_violations"][0])

    def test_validation_exception_records_blocking_failure(self) -> None:
        class RaisingValidation:
            def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
                raise ValueError("validation error")

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=RaisingValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-validation-fail"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["deep_validation"]["status"], "fail")
        self.assertEqual(len(outcome.report["deep_validation"]["blocking_failures"]), 1)
        self.assertIn("DEEP_VALIDATION_FAILED", outcome.report["deep_validation"]["blocking_failures"][0])

    def test_synthesis_exception_returns_partial_with_reason(self) -> None:
        class RaisingSynthesis:
            def synthesize(
                self,
                context: PostPushContext,
                cleanup: CleanupResult,
                validation: DeepValidationResult,
                audit: AuditResult,
                config: PipelineConfig,
            ) -> SynthesisResult:
                raise OSError("synthesis failed")

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=RaisingSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-synthesis-fail"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.report["synthesis"]["status"], "partial")
        self.assertIsNotNone(outcome.report["synthesis"].get("partial_reason"))
        self.assertIn("SYNTHESIS_FAILED", outcome.report["synthesis"]["partial_reason"])

    def test_audit_agent_exception_recorded_in_errors(self) -> None:
        class RaisingAuditAgent:
            name = "raiser"

            def run(
                self,
                context: PostPushContext,
                validation: DeepValidationResult,
                config: PipelineConfig,
            ) -> list[Finding]:
                raise RuntimeError("agent crashed")

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[RaisingAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-audit-fail"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.report["agents"]["errors"], 1)
        self.assertEqual(outcome.exit_code, 1)
        self.assertIn("audit_findings", outcome.report["artifacts"])

    def test_full_profile_blocks_when_all_audit_agents_fail(self) -> None:
        class RaisingAuditAgent:
            name = "adversarial"

            def run(
                self,
                context: PostPushContext,
                validation: DeepValidationResult,
                config: PipelineConfig,
            ) -> list[Finding]:
                _ = (context, validation, config)
                raise RuntimeError("agent crashed")

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[RaisingAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context_with_repo(run_id="run-full-audit-crash", profile="full"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["overall_result"], "fail")

    def test_light_profile_keeps_audit_agent_failure_advisory(self) -> None:
        class RaisingAuditAgent:
            name = "adversarial"

            def run(
                self,
                context: PostPushContext,
                validation: DeepValidationResult,
                config: PipelineConfig,
            ) -> list[Finding]:
                _ = (context, validation, config)
                raise RuntimeError("agent crashed")

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[RaisingAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context_with_repo(run_id="run-light-audit-crash", profile="light"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.report["overall_result"], "pass")

    def test_dedupe_closes_resolved_findings(self) -> None:
        class ToggleAuditAgent:
            name = "reviewer"

            def __init__(self) -> None:
                self._emit = True

            def run(
                self,
                context: PostPushContext,
                validation: DeepValidationResult,
                config: PipelineConfig,
            ) -> list[Finding]:
                _ = (context, validation, config)
                if not self._emit:
                    return []
                self._emit = False
                return [
                    Finding(
                        agent="reviewer",
                        finding_id="REV-1",
                        severity=Severity.MEDIUM,
                        title="Initial finding",
                        evidence=["src/a.py:1"],
                        recommendation="Fix",
                        dedupe_key="reviewer:initial",
                    )
                ]

        dedupe = InMemoryDedupeStore()
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[ToggleAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=dedupe,
        )
        ctx1 = PostPushContext(
            task_id="TASK-001",
            repo=str(self.repo_root),
            branch="main",
            sha="sha-1",
            pr="482",
            profile="full",
            identity=WorkItemIdentity(group_id="pr-482", commit_sha="sha-1", run_id="run-dedupe-1"),
        )
        ctx2 = PostPushContext(
            task_id="TASK-001",
            repo=str(self.repo_root),
            branch="main",
            sha="sha-2",
            pr="482",
            profile="full",
            identity=WorkItemIdentity(group_id="pr-482", commit_sha="sha-2", run_id="run-dedupe-2"),
        )
        first = pipeline.run(context=ctx1, config=PipelineConfig())
        second = pipeline.run(context=ctx2, config=PipelineConfig())
        self.assertEqual(first.report["agents"]["new_findings"], 1)
        self.assertEqual(second.report["agents"]["new_findings"], 0)
        self.assertEqual(dedupe.load_open_keys("pr-482"), set())
        audit_payload = Path(second.report["artifacts"]["audit_findings"]).read_text(encoding="utf-8")
        self.assertIn("resolved_rollups", audit_payload)

    def test_rerun_reuses_passed_stages_for_same_sha(self) -> None:
        class CountingCleanup:
            def __init__(self) -> None:
                self.calls = 0

            def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
                _ = (context, config)
                self.calls += 1
                return CleanupResult(status=StageStatus.PASS)

        class FailOnceValidation:
            def __init__(self) -> None:
                self.calls = 0

            def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
                _ = (context, config)
                self.calls += 1
                if self.calls == 1:
                    return DeepValidationResult(
                        status=StageStatus.FAIL,
                        checks=[ValidationCheck(name="unit-tests", status=StageStatus.FAIL, blocking=True)],
                        blocking_failures=["unit-tests"],
                    )
                return DeepValidationResult(
                    status=StageStatus.PASS,
                    checks=[ValidationCheck(name="unit-tests", status=StageStatus.PASS, blocking=True)],
                )

        cleanup = CountingCleanup()
        validation = FailOnceValidation()
        pipeline = PostPushPipeline(
            cleanup=cleanup,
            validation=validation,
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        first = pipeline.run(context=self._context_with_repo(run_id="run-cache-1"), config=PipelineConfig())
        second = pipeline.run(context=self._context_with_repo(run_id="run-cache-2"), config=PipelineConfig())
        self.assertEqual(first.exit_code, 1)
        self.assertEqual(second.exit_code, 0)
        self.assertEqual(cleanup.calls, 1, "cleanup should be reused from cache on second run")
        self.assertEqual(validation.calls, 2, "failed validation should rerun on second run")

    def test_overall_result_fail_on_cleanup_policy_violation(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=_CleanupWithViolation(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-cleanup-violation"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.report["overall_result"], "fail")
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["cleanup"]["status"], "pass")
        self.assertGreater(len(outcome.report["cleanup"]["policy_violations"]), 0)

    def test_overall_result_fail_on_merge_decision_block(self) -> None:
        class HighRiskAuditAgent:
            name = "reviewer"

            def run(
                self,
                context: PostPushContext,
                validation: DeepValidationResult,
                config: PipelineConfig,
            ) -> list[Finding]:
                _ = (context, validation, config)
                return [
                    Finding(
                        agent=self.name,
                        finding_id="REV-HI-1",
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
                ]

        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[HighRiskAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-merge-block"),
            config=PipelineConfig(),
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.report["overall_result"], "fail")
        self.assertEqual(outcome.report["merge_decision"]["decision"], "BLOCK")
        self.assertEqual(outcome.report["merge_decision"]["decision_reason"], "risk_escalation")

    def test_report_artifacts_contain_all_expected_keys(self) -> None:
        pipeline = PostPushPipeline(
            cleanup=SafeNoopCleanup(),
            validation=_PassValidation(),
            audit_agents=[NoopAuditAgent()],
            synthesis=BaselineSynthesis(),
            artifact_store=FileArtifactStore(repo_root=self.repo_root),
            dedupe_store=InMemoryDedupeStore(),
        )
        outcome = pipeline.run(
            context=self._context(run_id="run-artifacts"),
            config=PipelineConfig(),
        )
        artifacts = outcome.report["artifacts"]
        for key in (
            "cleanup",
            "deep_validation",
            "audit_findings",
            "follow_up_tasks",
            "merge_decision",
            "current_state_summary",
            "post_push_report",
        ):
            self.assertIn(key, artifacts, msg=f"missing artifact key: {key}")
            self.assertTrue(Path(artifacts[key]).exists(), msg=f"artifact file missing: {artifacts[key]}")


class _CleanupWithViolation:
    def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
        return CleanupResult(
            status=StageStatus.PASS,
            cleaned=[],
            skipped=[],
            policy_violations=["manual cleanup required"],
        )


class _PassValidation:
    def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        _ = (context, config)
        return DeepValidationResult(
            status=StageStatus.PASS,
            checks=[
                ValidationCheck(name="unit-tests", status=StageStatus.PASS, blocking=True),
                ValidationCheck(name="integration-tests", status=StageStatus.PASS, blocking=True),
            ],
            blocking_failures=[],
            mutation_mode=MutationMode.SAMPLE,
            mutation_score=82,
            mutation_threshold=70,
        )


if __name__ == "__main__":
    unittest.main()
