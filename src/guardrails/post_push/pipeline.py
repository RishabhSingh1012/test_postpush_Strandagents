from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from beartype import beartype
from beartype.typing import Any

from .constants import (
    ARTIFACT_KEY_POST_PUSH_REPORT,
    AUDIT_FINDINGS_FILENAME,
    CLEANUP_REPORT_FILENAME,
    CURRENT_STATE_SUMMARY_FILENAME,
    DEEP_VALIDATION_REPORT_FILENAME,
    FOLLOW_UP_TASKS_FILENAME,
    MERGE_DECISION_BLOCK,
    MERGE_DECISION_FILENAME,
    OVERALL_RESULT_FAIL,
    OVERALL_RESULT_PASS,
    POST_PUSH_REPORT_FILENAME,
    STAGE_CACHE_FILENAME,
)
from .contracts import (
    AuditResult,
    CleanupSkip,
    CleanupResult,
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
)
from .interfaces import (
    ArtifactStore,
    AuditAgent,
    CleanupAdapter,
    DedupeStore,
    MergeDecisionAdapter,
    SynthesisAdapter,
    TaskTracker,
    ValidationAdapter,
)
from .merge_decision import (
    FindingScores,
    MergeDecisionFinding,
    MergeDecisionHardGate,
    MergeDecisionResult,
    MergeDecisionSummary,
    RuleBasedMergeDecisionTriage,
    TicketPayload,
)


@dataclass(frozen=True, slots=True)
class PostPushOutcome:
    exit_code: int
    run_dir: str
    report: dict[str, Any]


class PostPushPipeline:
    __slots__ = (
        "_cleanup",
        "_validation",
        "_audit_agents",
        "_synthesis",
        "_artifacts",
        "_dedupe",
        "_merge_decision",
        "_task_tracker",
    )

    def __init__(
        self,
        cleanup: CleanupAdapter,
        validation: ValidationAdapter,
        audit_agents: list[AuditAgent],
        synthesis: SynthesisAdapter,
        artifact_store: ArtifactStore,
        dedupe_store: DedupeStore | None = None,
        merge_decision: MergeDecisionAdapter | None = None,
        task_tracker: TaskTracker | None = None,
    ) -> None:
        self._cleanup = cleanup
        self._validation = validation
        self._audit_agents = audit_agents
        self._synthesis = synthesis
        self._artifacts = artifact_store
        self._dedupe = dedupe_store
        self._merge_decision = merge_decision or RuleBasedMergeDecisionTriage()
        self._task_tracker = task_tracker

    @beartype
    def run(self, context: PostPushContext, config: PipelineConfig) -> PostPushOutcome:
        self._artifacts.init_run(context=context, config=config)
        prior_cache = self._load_prior_stage_cache(context=context, config=config)
        stage_cache: dict[str, dict[str, Any]] = {}

        cleanup_fp = self._fingerprint(
            {
                "stage": "cleanup",
                "sha": context.sha,
                "task_id": context.task_id,
                "branch": context.branch,
                "cleanup_mode": config.cleanup_mode,
                "workspace": str(context.workspace_path),
            }
        )
        cleanup_result = self._reuse_or_run_stage(
            stage_name="cleanup",
            fingerprint=cleanup_fp,
            prior_cache=prior_cache,
            runner=lambda: self._run_cleanup(context=context, config=config),
            parser=self._cleanup_from_dict,
        )
        stage_cache["cleanup"] = self._stage_cache_entry(cleanup_fp, cleanup_result.status, cleanup_result.to_dict())

        validation_fp = self._fingerprint(
            {
                "stage": "deep_validation",
                "sha": context.sha,
                "profile": context.profile,
                "mutation_mode": config.mutation_mode.value,
            }
        )
        validation_result = self._reuse_or_run_stage(
            stage_name="deep_validation",
            fingerprint=validation_fp,
            prior_cache=prior_cache,
            runner=lambda: self._run_validation(context=context, config=config),
            parser=self._validation_from_dict,
        )
        stage_cache["deep_validation"] = self._stage_cache_entry(
            validation_fp,
            validation_result.status,
            validation_result.to_dict(),
        )

        if validation_result.blocking_failures and not config.run_audits_on_validation_failure:
            audit_result = AuditResult(
                status=StageStatus.SKIP,
                agent_findings={agent.name: 0 for agent in self._audit_agents},
                failed_agents=[],
            )
            audit_fp = self._fingerprint(
                {
                    "stage": "audit",
                    "sha": context.sha,
                    "profile": context.profile,
                    "skip_due_to_validation": True,
                    "validation": validation_result.to_dict(),
                }
            )
        else:
            audit_fp = self._fingerprint(
                {
                    "stage": "audit",
                    "sha": context.sha,
                    "profile": context.profile,
                    "changed_files": sorted(context.changed_files),
                    "validation": validation_result.to_dict(),
                }
            )
            audit_result = self._reuse_or_run_stage(
                stage_name="audit",
                fingerprint=audit_fp,
                prior_cache=prior_cache,
                runner=lambda: self._run_audits(context=context, validation=validation_result, config=config),
                parser=self._audit_from_dict,
            )
        stage_cache["audit"] = self._stage_cache_entry(audit_fp, audit_result.status, audit_result.to_dict())

        synthesis_fp = self._fingerprint(
            {
                "stage": "synthesis",
                "sha": context.sha,
                "cleanup": cleanup_result.to_dict(),
                "validation": validation_result.to_dict(),
                "audit": audit_result.to_dict(),
            }
        )
        synthesis_result = self._reuse_or_run_stage(
            stage_name="synthesis",
            fingerprint=synthesis_fp,
            prior_cache=prior_cache,
            runner=lambda: self._run_synthesis(
                context=context,
                cleanup=cleanup_result,
                validation=validation_result,
                audit=audit_result,
                config=config,
            ),
            parser=self._synthesis_from_dict,
        )
        synthesis_payload = synthesis_result.to_dict()
        synthesis_payload["summary_markdown"] = synthesis_result.summary_markdown
        stage_cache["synthesis"] = self._stage_cache_entry(synthesis_fp, synthesis_result.status, synthesis_payload)

        merge_fp = self._fingerprint(
            {
                "stage": "merge_decision",
                "sha": context.sha,
                "profile": context.profile,
                "validation": validation_result.to_dict(),
                "audit": audit_result.to_dict(),
            }
        )
        merge_decision_result = self._reuse_or_run_stage(
            stage_name="merge_decision",
            fingerprint=merge_fp,
            prior_cache=prior_cache,
            runner=lambda: self._run_merge_decision(
                context=context,
                validation=validation_result,
                audit=audit_result,
                config=config,
            ),
            parser=self._merge_decision_from_dict,
        )
        merge_status = StageStatus.FAIL if merge_decision_result.decision == MERGE_DECISION_BLOCK else StageStatus.PASS
        stage_cache["merge_decision"] = self._stage_cache_entry(merge_fp, merge_status, merge_decision_result.to_dict())
        if self._task_tracker and audit_result.new_findings:
            for finding in audit_result.new_findings:
                triaged = next(
                    (m for m in merge_decision_result.findings if m.id == finding.finding_id),
                    None,
                )
                priority = triaged.priority if triaged else "P2"
                self._task_tracker.create_task_for_finding(finding, priority=priority)

        follow_up_tasks = self._derive_follow_up_tasks(
            validation_result,
            audit_result,
            synthesis_result,
            merge_decision_result,
        )
        overall_result = self._compute_overall_result(
            context=context,
            cleanup=cleanup_result,
            validation=validation_result,
            merge_decision=merge_decision_result,
            audit=audit_result,
        )
        exit_code = 0 if overall_result == OVERALL_RESULT_PASS else 1

        artifact_paths = self._write_artifacts(
            cleanup=cleanup_result,
            validation=validation_result,
            audit=audit_result,
            synthesis=synthesis_result,
            merge_decision=merge_decision_result,
            follow_up_tasks=follow_up_tasks,
        )
        stage_cache_path = self._write_stage_cache(context=context, config=config, stage_cache=stage_cache)
        artifact_paths["stage_cache"] = str(stage_cache_path)

        agent_counts = self._agent_breakdown(audit_result)

        report = {
            "work_item_id": {
                "group_id": context.identity.group_id,
                "commit_sha": context.identity.commit_sha,
                "run_id": context.identity.run_id,
            },
            "repo": context.repo,
            "branch": context.branch,
            "sha": context.sha,
            "pr": context.pr,
            "profile": context.profile,
            "runtime_id": context.runtime_id,
            "overall_result": overall_result,
            "cleanup": cleanup_result.to_dict(),
            "deep_validation": validation_result.to_dict(),
            "agents": {
                "count": len(self._audit_agents),
                "new_findings": len(audit_result.new_findings),
                "rollups": len(audit_result.unresolved_rollups),
                "errors": len(audit_result.errors),
                "failed_agents": audit_result.failed_agents,
                "adversarial": {"findings": agent_counts["adversarial"]},
                "optimization": {"findings": agent_counts["optimization"]},
                "optimisation": {"findings": agent_counts["optimization"]},
                "reviewer": {"findings": agent_counts["reviewer"]},
            },
            "synthesis": {
                **synthesis_result.to_dict(),
                "path": artifact_paths["current_state_summary"],
            },
            "merge_decision": merge_decision_result.to_dict(),
            "next_actions": follow_up_tasks,
            "artifacts": artifact_paths,
            "generated_at": _utc_now(),
            "rerun": self._suggest_rerun(context),
        }
        index_path = self._artifacts.write_json(POST_PUSH_REPORT_FILENAME, report)
        report["artifacts"][ARTIFACT_KEY_POST_PUSH_REPORT] = str(index_path)
        return PostPushOutcome(exit_code=exit_code, run_dir=str(self._artifacts.run_dir), report=report)

    def _agent_breakdown(self, audit: AuditResult) -> dict[str, int]:
        return {
            "adversarial": int(audit.agent_findings.get("adversarial", 0)),
            "optimization": int(audit.agent_findings.get("optimization", 0)),
            "reviewer": int(audit.agent_findings.get("reviewer", 0)),
        }

    def _load_prior_stage_cache(self, context: PostPushContext, config: PipelineConfig) -> dict[str, Any]:
        root = Path(context.repo) / config.artifacts_root / context.identity.group_id / context.identity.commit_sha
        if not root.is_dir():
            return {}

        run_dirs = [path for path in root.iterdir() if path.is_dir() and path.name != context.identity.run_id]
        run_dirs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for run_dir in run_dirs:
            cache_file = run_dir / STAGE_CACHE_FILENAME
            if not cache_file.is_file():
                continue
            try:
                parsed = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            stages = parsed.get("stages")
            if not isinstance(stages, dict):
                continue
            return parsed
        return {}

    def _write_stage_cache(
        self,
        context: PostPushContext,
        config: PipelineConfig,
        stage_cache: dict[str, dict[str, Any]],
    ) -> Path:
        payload = {
            "schema_version": 1,
            "context": {
                "group_id": context.identity.group_id,
                "commit_sha": context.identity.commit_sha,
                "run_id": context.identity.run_id,
                "profile": context.profile,
                "cleanup_mode": config.cleanup_mode,
                "mutation_mode": config.mutation_mode.value,
            },
            "stages": stage_cache,
            "generated_at": _utc_now(),
        }
        return self._artifacts.write_json(STAGE_CACHE_FILENAME, payload)

    def _reuse_or_run_stage(
        self,
        stage_name: str,
        fingerprint: str,
        prior_cache: dict[str, Any],
        runner: Any,
        parser: Any,
    ) -> Any:
        cached = self._cached_stage_result(stage_name=stage_name, fingerprint=fingerprint, prior_cache=prior_cache, parser=parser)
        if cached is not None:
            return cached
        return runner()

    def _cached_stage_result(
        self,
        stage_name: str,
        fingerprint: str,
        prior_cache: dict[str, Any],
        parser: Any,
    ) -> Any | None:
        stages = prior_cache.get("stages")
        if not isinstance(stages, dict):
            return None
        stage = stages.get(stage_name)
        if not isinstance(stage, dict):
            return None
        if stage.get("fingerprint") != fingerprint:
            return None
        if stage.get("status") != StageStatus.PASS.value:
            return None
        raw = stage.get("result")
        if not isinstance(raw, dict):
            return None
        try:
            return parser(raw)
        except Exception:
            return None

    def _stage_cache_entry(
        self,
        fingerprint: str,
        status: StageStatus,
        result_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "fingerprint": fingerprint,
            "status": status.value,
            "result": result_payload,
        }

    def _fingerprint(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _cleanup_from_dict(self, payload: dict[str, Any]) -> CleanupResult:
        skipped_raw = payload.get("skipped", [])
        skipped = []
        if isinstance(skipped_raw, list):
            for item in skipped_raw:
                if isinstance(item, dict):
                    skipped.append(
                        CleanupSkip(
                            item=str(item.get("item", "")),
                            reason=str(item.get("reason", "")),
                        )
                    )
        return CleanupResult(
            status=_stage_status(str(payload.get("status", StageStatus.FAIL.value))),
            cleaned=[str(item) for item in payload.get("cleaned", []) if isinstance(item, str)],
            skipped=skipped,
            policy_violations=[
                str(item) for item in payload.get("policy_violations", []) if isinstance(item, str)
            ],
        )

    def _validation_from_dict(self, payload: dict[str, Any]) -> DeepValidationResult:
        checks_raw = payload.get("checks", [])
        checks: list[ValidationCheck] = []
        if isinstance(checks_raw, list):
            for item in checks_raw:
                if not isinstance(item, dict):
                    continue
                checks.append(
                    ValidationCheck(
                        name=str(item.get("name", "unknown")),
                        status=_stage_status(str(item.get("status", StageStatus.FAIL.value))),
                        blocking=bool(item.get("blocking", True)),
                        details=str(item.get("details", "")),
                    )
                )
        mutation = payload.get("mutation", {})
        mutation_mode_raw = mutation.get("mode") if isinstance(mutation, dict) else "off"
        return DeepValidationResult(
            status=_stage_status(str(payload.get("status", StageStatus.FAIL.value))),
            checks=checks,
            blocking_failures=[
                str(item) for item in payload.get("blocking_failures", []) if isinstance(item, str)
            ],
            non_blocking_notes=[
                str(item) for item in payload.get("non_blocking_notes", []) if isinstance(item, str)
            ],
            mutation_mode=_mutation_mode(str(mutation_mode_raw)),
            mutation_score=_optional_int(mutation.get("score") if isinstance(mutation, dict) else None),
            mutation_threshold=_optional_int(mutation.get("threshold") if isinstance(mutation, dict) else None),
        )

    def _audit_from_dict(self, payload: dict[str, Any]) -> AuditResult:
        new_findings_raw = payload.get("new_findings", [])
        findings: list[Finding] = []
        if isinstance(new_findings_raw, list):
            for item in new_findings_raw:
                if isinstance(item, dict):
                    findings.append(_finding_from_dict(item))
        return AuditResult(
            status=_stage_status(str(payload.get("status", StageStatus.FAIL.value))),
            new_findings=findings,
            unresolved_rollups=_dict_string_list(payload.get("unresolved_rollups")),
            resolved_rollups=_dict_string_list(payload.get("resolved_rollups")),
            known_issue_refs=_dict_string_list(payload.get("known_issue_refs")),
            agent_findings=_string_int_dict(payload.get("agent_findings")),
            failed_agents=[str(item) for item in payload.get("failed_agents", []) if isinstance(item, str)],
            errors=[str(item) for item in payload.get("errors", []) if isinstance(item, str)],
        )

    def _synthesis_from_dict(self, payload: dict[str, Any]) -> SynthesisResult:
        return SynthesisResult(
            status=_stage_status(str(payload.get("status", StageStatus.FAIL.value))),
            summary_markdown=str(payload.get("summary_markdown", "# Current State Summary")),
            risks=[str(item) for item in payload.get("risks", []) if isinstance(item, str)],
            follow_up_tasks=[str(item) for item in payload.get("follow_up_tasks", []) if isinstance(item, str)],
            partial_reason=str(payload["partial_reason"]) if payload.get("partial_reason") is not None else None,
        )

    def _merge_decision_from_dict(self, payload: dict[str, Any]) -> MergeDecisionResult:
        hard_gates_raw = payload.get("hard_gates", [])
        hard_gates: list[MergeDecisionHardGate] = []
        if isinstance(hard_gates_raw, list):
            for item in hard_gates_raw:
                if not isinstance(item, dict):
                    continue
                hard_gates.append(
                    MergeDecisionHardGate(
                        name=str(item.get("name", "unknown")),
                        status=str(item.get("status", "FAIL")),
                        blocking=bool(item.get("blocking", True)),
                    )
                )

        findings_raw = payload.get("findings", [])
        findings: list[MergeDecisionFinding] = []
        if isinstance(findings_raw, list):
            for item in findings_raw:
                if not isinstance(item, dict):
                    continue
                scores_raw = item.get("scores", {})
                ticket_raw = item.get("ticket", {})
                scores = FindingScores(
                    severity=_optional_int(scores_raw.get("severity") if isinstance(scores_raw, dict) else None) or 0,
                    likelihood=_optional_int(scores_raw.get("likelihood") if isinstance(scores_raw, dict) else None) or 0,
                    blast_radius=_optional_int(scores_raw.get("blast_radius") if isinstance(scores_raw, dict) else None) or 0,
                    detectability=_optional_int(scores_raw.get("detectability") if isinstance(scores_raw, dict) else None) or 0,
                    raw_score=_optional_int(scores_raw.get("raw_score") if isinstance(scores_raw, dict) else None) or 0,
                    score=_optional_int(scores_raw.get("score") if isinstance(scores_raw, dict) else None) or 0,
                )
                ticket = TicketPayload(
                    required=bool(ticket_raw.get("required", True)) if isinstance(ticket_raw, dict) else True,
                    title=str(ticket_raw.get("title", "")) if isinstance(ticket_raw, dict) else "",
                    context=str(ticket_raw.get("context", "")) if isinstance(ticket_raw, dict) else "",
                    acceptance_criteria=[
                        str(value)
                        for value in (ticket_raw.get("acceptance_criteria", []) if isinstance(ticket_raw, dict) else [])
                        if isinstance(value, str)
                    ],
                    owner=str(ticket_raw.get("owner", "")) if isinstance(ticket_raw, dict) else "",
                    priority=str(ticket_raw.get("priority", "P2")) if isinstance(ticket_raw, dict) else "P2",
                )
                findings.append(
                    MergeDecisionFinding(
                        id=str(item.get("id", "")),
                        title=str(item.get("title", "")),
                        category=str(item.get("category", "other")),
                        finding_type=str(item.get("type", "soft")),
                        scores=scores,
                        decision=str(item.get("decision", "NON-BLOCK")),
                        priority=str(item.get("priority", "P2")),
                        rationale=str(item.get("rationale", "")),
                        ticket=ticket,
                    )
                )

        summary_raw = payload.get("summary", {})
        summary = MergeDecisionSummary(
            blocking_count=_optional_int(summary_raw.get("blocking_count") if isinstance(summary_raw, dict) else None) or 0,
            non_blocking_count=_optional_int(
                summary_raw.get("non_blocking_count") if isinstance(summary_raw, dict) else None
            )
            or 0,
            max_soft_score=_optional_int(summary_raw.get("max_soft_score") if isinstance(summary_raw, dict) else None) or 0,
            required_actions=[
                str(value)
                for value in (summary_raw.get("required_actions", []) if isinstance(summary_raw, dict) else [])
                if isinstance(value, str)
            ],
        )
        return MergeDecisionResult(
            decision=str(payload.get("decision", "NON-BLOCK")),
            decision_reason=str(payload.get("decision_reason", "advisory_only")),
            priority=str(payload.get("priority", "P2")),
            merge_status=str(payload.get("merge_status", "ALLOW")),
            hard_gates=hard_gates,
            findings=findings,
            summary=summary,
        )

    def _run_cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
        try:
            return self._cleanup.cleanup(context=context, config=config)
        except Exception as exc:  # pragma: no cover - defensive path
            return CleanupResult(
                status=StageStatus.FAIL,
                policy_violations=[self._serialize_exception("post-push", "cleanup", "CLEANUP_FAILED", context.task_id, exc)],
            )

    def _run_validation(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        try:
            return self._validation.validate(context=context, config=config)
        except Exception as exc:  # pragma: no cover - defensive path
            payload = self._serialize_exception(
                "post-push",
                "deep-validation",
                "DEEP_VALIDATION_FAILED",
                context.task_id,
                exc,
            )
            return DeepValidationResult(
                status=StageStatus.FAIL,
                blocking_failures=[payload],
            )

    def _run_audits(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        config: PipelineConfig,
    ) -> AuditResult:
        all_findings: list[Finding] = []
        errors: list[str] = []
        agent_findings: dict[str, int] = {}
        failed_agents: list[str] = []
        for agent in self._audit_agents:
            try:
                findings = agent.run(context=context, validation=validation, config=config)
                agent_findings[agent.name] = len(findings)
                all_findings.extend(findings)
            except Exception as exc:  # pragma: no cover - defensive path
                agent_findings[agent.name] = 0
                failed_agents.append(agent.name)
                errors.append(
                    self._serialize_exception(
                        stage="post-push",
                        step=f"audit-{agent.name}",
                        code="AUDIT_AGENT_FAILED",
                        task_id=context.task_id,
                        exc=exc,
                    )
                )

        return self._dedupe_findings(
            context=context,
            findings=all_findings,
            errors=errors,
            agent_findings=agent_findings,
            failed_agents=failed_agents,
        )

    def _dedupe_findings(
        self,
        context: PostPushContext,
        findings: list[Finding],
        errors: list[str],
        agent_findings: dict[str, int],
        failed_agents: list[str],
    ) -> AuditResult:
        if self._dedupe is None:
            status = StageStatus.FAIL if errors else StageStatus.PASS
            return AuditResult(
                status=status,
                new_findings=findings,
                agent_findings=agent_findings,
                failed_agents=failed_agents,
                errors=errors,
            )

        prior_keys = self._dedupe.load_open_keys(context.identity.group_id)
        new_findings: list[Finding] = []
        rollups: list[dict[str, str]] = []
        resolved: list[dict[str, str]] = []
        known_issue_refs: list[dict[str, str]] = []
        current_open: set[str] = set()

        for finding in findings:
            known_ref = self._dedupe.known_issue_ref(finding.dedupe_key)
            if known_ref:
                known_issue_refs.append(
                    {
                        "finding_id": finding.finding_id,
                        "dedupe_key": finding.dedupe_key,
                        "issue_ref": known_ref,
                    }
                )
                continue

            current_open.add(finding.dedupe_key)
            if finding.dedupe_key in prior_keys:
                rollups.append({"finding_id": finding.finding_id, "title": finding.title})
            else:
                new_findings.append(finding)

        for dedupe_key in sorted(prior_keys - current_open):
            resolved.append({"dedupe_key": dedupe_key})

        self._dedupe.save_open_keys(context.identity.group_id, current_open)
        status = StageStatus.FAIL if errors else StageStatus.PASS
        return AuditResult(
            status=status,
            new_findings=new_findings,
            unresolved_rollups=rollups,
            resolved_rollups=resolved,
            known_issue_refs=known_issue_refs,
            agent_findings=agent_findings,
            failed_agents=failed_agents,
            errors=errors,
        )

    def _run_synthesis(
        self,
        context: PostPushContext,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> SynthesisResult:
        try:
            return self._synthesis.synthesize(
                context=context,
                cleanup=cleanup,
                validation=validation,
                audit=audit,
                config=config,
            )
        except Exception as exc:  # pragma: no cover - defensive path
            return SynthesisResult(
                status=StageStatus.PARTIAL,
                summary_markdown="# Current State Summary\n\nSynthesis failed.",
                partial_reason=self._serialize_exception(
                    "post-push",
                    "synthesis",
                    "SYNTHESIS_FAILED",
                    context.task_id,
                    exc,
                ),
            )

    def _write_artifacts(
        self,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        audit: AuditResult,
        synthesis: SynthesisResult,
        merge_decision: MergeDecisionResult,
        follow_up_tasks: list[str],
    ) -> dict[str, str]:
        cleanup_path = self._artifacts.write_json(CLEANUP_REPORT_FILENAME, {"cleanup": cleanup.to_dict()})
        validation_path = self._artifacts.write_json(
            DEEP_VALIDATION_REPORT_FILENAME,
            {"deep_validation": validation.to_dict()},
        )
        findings_path = self._artifacts.write_json(
            AUDIT_FINDINGS_FILENAME,
            {"audit": audit.to_dict()},
        )
        follow_up_path = self._artifacts.write_json(
            FOLLOW_UP_TASKS_FILENAME,
            {"tasks": follow_up_tasks},
        )
        merge_decision_path = self._artifacts.write_json(
            MERGE_DECISION_FILENAME,
            {"merge_decision": merge_decision.to_dict()},
        )
        summary_path = self._artifacts.write_text(CURRENT_STATE_SUMMARY_FILENAME, synthesis.summary_markdown)
        return {
            "cleanup": str(cleanup_path),
            "deep_validation": str(validation_path),
            "audit_findings": str(findings_path),
            "follow_up_tasks": str(follow_up_path),
            "merge_decision": str(merge_decision_path),
            "current_state_summary": str(summary_path),
        }

    def _compute_overall_result(
        self,
        context: PostPushContext,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        merge_decision: MergeDecisionResult,
        audit: AuditResult,
    ) -> str:
        profile = context.profile.lower().strip()
        if cleanup.policy_violations:
            return OVERALL_RESULT_FAIL
        if validation.blocking_failures:
            return OVERALL_RESULT_FAIL
        if validation.status == StageStatus.FAIL:
            return OVERALL_RESULT_FAIL
        if profile == "full" and self._audit_agents and len(audit.failed_agents) == len(self._audit_agents):
            return OVERALL_RESULT_FAIL
        if merge_decision.decision == MERGE_DECISION_BLOCK:
            return OVERALL_RESULT_FAIL
        return OVERALL_RESULT_PASS

    def _derive_follow_up_tasks(
        self,
        validation: DeepValidationResult,
        audit: AuditResult,
        synthesis: SynthesisResult,
        merge_decision: MergeDecisionResult,
    ) -> list[str]:
        tasks = [f"Resolve blocking issue: {item}" for item in validation.blocking_failures]
        for finding in audit.new_findings:
            tasks.append(f"{finding.finding_id}: {finding.recommendation}")
        tasks.extend(synthesis.follow_up_tasks)
        tasks.extend(merge_decision.summary.required_actions)
        return tasks

    def _run_merge_decision(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> MergeDecisionResult:
        return self._merge_decision.decide(
            context=context,
            validation=validation,
            audit=audit,
            config=config,
        )

    def _suggest_rerun(self, context: PostPushContext) -> str:
        args = [f"--task-id {context.task_id}", f"--profile {context.profile}", f"--sha {context.sha}"]
        if context.pr:
            args.append(f"--pr {context.pr}")
        if context.runtime_id:
            args.append(f"--runtime-id {context.runtime_id}")
        return "guardrails post-push " + " ".join(args)

    def _serialize_exception(
        self,
        stage: str,
        step: str,
        code: str,
        task_id: str,
        exc: Exception,
    ) -> str:
        envelope = ErrorEnvelope(
            error=True,
            stage=stage,
            step=step,
            code=code,
            message=str(exc),
            task_id=task_id,
        )
        return str(envelope.to_dict())


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _stage_status(raw: str) -> StageStatus:
    value = (raw or "").strip().lower()
    mapping = {
        StageStatus.PASS.value: StageStatus.PASS,
        StageStatus.FAIL.value: StageStatus.FAIL,
        StageStatus.PARTIAL.value: StageStatus.PARTIAL,
        StageStatus.SKIP.value: StageStatus.SKIP,
    }
    return mapping.get(value, StageStatus.FAIL)


def _mutation_mode(raw: str) -> MutationMode:
    value = (raw or "").strip().lower()
    mapping = {
        "off": MutationMode.OFF,
        "sample": MutationMode.SAMPLE,
        "full": MutationMode.FULL,
    }
    return mapping.get(value, MutationMode.OFF)


def _severity(raw: str) -> Severity:
    value = (raw or "").strip().lower()
    mapping = {
        Severity.CRITICAL.value: Severity.CRITICAL,
        Severity.HIGH.value: Severity.HIGH,
        Severity.MEDIUM.value: Severity.MEDIUM,
        Severity.LOW.value: Severity.LOW,
        Severity.INFO.value: Severity.INFO,
    }
    return mapping.get(value, Severity.MEDIUM)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dict_string_list(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed: dict[str, str] = {}
        for key, value in item.items():
            if isinstance(key, str) and isinstance(value, str):
                parsed[key] = value
        if parsed:
            out.append(parsed)
    return out


def _string_int_dict(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        num = _optional_int(value)
        out[key] = num if num is not None else 0
    return out


def _finding_from_dict(raw: dict[str, Any]) -> Finding:
    evidence = raw.get("evidence", [])
    risk = raw.get("risk", {})
    return Finding(
        agent=str(raw.get("agent", "reviewer")),
        finding_id=str(raw.get("finding_id", "F-UNKNOWN")),
        severity=_severity(str(raw.get("severity", Severity.MEDIUM.value))),
        title=str(raw.get("title", "Review finding")),
        evidence=[str(item) for item in evidence if isinstance(item, str)] if isinstance(evidence, list) else [],
        recommendation=str(raw.get("recommendation", "Investigate and address the issue.")),
        dedupe_key=str(raw.get("dedupe_key", "finding:unknown")),
        category=str(raw.get("category", "other")),
        finding_type=str(raw.get("type", "soft")),
        owner=str(raw["owner"]) if raw.get("owner") is not None else None,
        risk_severity=_optional_int(risk.get("severity") if isinstance(risk, dict) else None),
        risk_likelihood=_optional_int(risk.get("likelihood") if isinstance(risk, dict) else None),
        risk_blast_radius=_optional_int(risk.get("blast_radius") if isinstance(risk, dict) else None),
        risk_detectability=_optional_int(risk.get("detectability") if isinstance(risk, dict) else None),
    )
