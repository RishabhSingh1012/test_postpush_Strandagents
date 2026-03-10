from __future__ import annotations

from beartype import beartype

from .contracts import (
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    Finding,
    MutationMode,
    PipelineConfig,
    PostPushContext,
    Severity,
    StageStatus,
    SynthesisResult,
    ValidationCheck,
)


class SafeNoopCleanup:
    __slots__ = ()

    @beartype
    def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
        _ = (context, config)
        return CleanupResult(status=StageStatus.PASS, cleaned=[], skipped=[], policy_violations=[])


class PassValidation:
    __slots__ = ()

    @beartype
    def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        _ = context
        checks = [
            ValidationCheck(name="unit-tests", status=StageStatus.PASS, blocking=True),
            ValidationCheck(name="integration-tests", status=StageStatus.PASS, blocking=True),
            ValidationCheck(name="type-check", status=StageStatus.PASS, blocking=True),
        ]
        return DeepValidationResult(
            status=StageStatus.PASS,
            checks=checks,
            blocking_failures=[],
            non_blocking_notes=[],
            mutation_mode=config.mutation_mode if isinstance(config.mutation_mode, MutationMode) else MutationMode.OFF,
        )


class NoopAuditAgent:
    __slots__ = ()
    name = "reviewer"

    @beartype
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
                finding_id="REV-001",
                severity=Severity.INFO,
                title="No critical issues discovered",
                evidence=["audit:empty"],
                recommendation="No action required",
                dedupe_key="reviewer:no-critical-issues",
            )
        ]


class BaselineSynthesis:
    __slots__ = ()

    @beartype
    def synthesize(
        self,
        context: PostPushContext,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> SynthesisResult:
        _ = config
        summary = "\n".join(
            [
                "# Current State Summary",
                "",
                f"- Repository: {context.repo}",
                f"- Branch: {context.branch}",
                f"- SHA: {context.sha}",
                f"- Profile: {context.profile}",
                f"- Cleanup status: {cleanup.status.value}",
                f"- Deep validation status: {validation.status.value}",
                f"- Audit status: {audit.status.value}",
                "",
                "## Interfaces",
                "- Interface discovery should be provided by repository adapters.",
                "",
                "## Architecture",
                "- Architecture summary should be provided by repository adapters.",
                "",
                "## Test Posture",
                "- Test posture should be provided by validation adapters.",
            ]
        )
        return SynthesisResult(
            status=StageStatus.PARTIAL if validation.blocking_failures else StageStatus.PASS,
            summary_markdown=summary,
            risks=list(validation.blocking_failures),
            follow_up_tasks=[],
            partial_reason="Blocking failures present" if validation.blocking_failures else None,
        )
