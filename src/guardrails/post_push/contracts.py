from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from beartype import beartype
from beartype.typing import Any

from .constants import ARTIFACTS_ROOT_DEFAULT
from .workspace import workspace_path_for


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class StageStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    SKIP = "skip"


class MutationMode(str, Enum):
    OFF = "off"
    SAMPLE = "sample"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class WorkItemIdentity:
    group_id: str
    commit_sha: str
    run_id: str


@dataclass(frozen=True, slots=True)
class PostPushContext:
    task_id: str
    repo: str
    branch: str
    sha: str
    identity: WorkItemIdentity
    profile: str = "light"
    pr: str | None = None
    changed_files: list[str] = field(default_factory=list)
    runtime_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    @beartype
    def workspace_path(self) -> Path:
        """Workspace directory per convention: repo_root / .agentic-workspaces / <task_id>."""
        return workspace_path_for(self.repo, self.task_id)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    cleanup_mode: str = "safe"
    mutation_mode: MutationMode = MutationMode.OFF
    run_audits_on_validation_failure: bool = True
    artifacts_root: str = ARTIFACTS_ROOT_DEFAULT


@dataclass(frozen=True, slots=True)
class CleanupSkip:
    item: str
    reason: str


@dataclass(frozen=True, slots=True)
class CleanupResult:
    status: StageStatus
    cleaned: list[str] = field(default_factory=list)
    skipped: list[CleanupSkip] = field(default_factory=list)
    policy_violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "cleaned": self.cleaned,
            "skipped": [asdict(item) for item in self.skipped],
            "policy_violations": self.policy_violations,
        }


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    status: StageStatus
    blocking: bool
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "blocking": self.blocking,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class DeepValidationResult:
    status: StageStatus
    checks: list[ValidationCheck] = field(default_factory=list)
    blocking_failures: list[str] = field(default_factory=list)
    non_blocking_notes: list[str] = field(default_factory=list)
    mutation_mode: MutationMode = MutationMode.OFF
    mutation_score: int | None = None
    mutation_threshold: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "checks": [check.to_dict() for check in self.checks],
            "blocking_failures": self.blocking_failures,
            "non_blocking_notes": self.non_blocking_notes,
            "mutation": {
                "mode": self.mutation_mode.value,
                "score": self.mutation_score,
                "threshold": self.mutation_threshold,
            },
        }


@dataclass(frozen=True, slots=True)
class Finding:
    agent: str
    finding_id: str
    severity: Severity
    title: str
    evidence: list[str]
    recommendation: str
    dedupe_key: str
    category: str = "other"
    finding_type: str = "soft"
    owner: str | None = None
    risk_severity: int | None = None
    risk_likelihood: int | None = None
    risk_blast_radius: int | None = None
    risk_detectability: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "finding_id": self.finding_id,
            "severity": self.severity.value,
            "title": self.title,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "dedupe_key": self.dedupe_key,
            "category": self.category,
            "type": self.finding_type,
            "owner": self.owner,
            "risk": {
                "severity": self.risk_severity,
                "likelihood": self.risk_likelihood,
                "blast_radius": self.risk_blast_radius,
                "detectability": self.risk_detectability,
            },
        }


@dataclass(frozen=True, slots=True)
class AuditResult:
    status: StageStatus
    new_findings: list[Finding] = field(default_factory=list)
    unresolved_rollups: list[dict[str, str]] = field(default_factory=list)
    resolved_rollups: list[dict[str, str]] = field(default_factory=list)
    known_issue_refs: list[dict[str, str]] = field(default_factory=list)
    agent_findings: dict[str, int] = field(default_factory=dict)
    failed_agents: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "new_findings": [finding.to_dict() for finding in self.new_findings],
            "unresolved_rollups": self.unresolved_rollups,
            "resolved_rollups": self.resolved_rollups,
            "known_issue_refs": self.known_issue_refs,
            "agent_findings": self.agent_findings,
            "failed_agents": self.failed_agents,
            "errors": self.errors,
        }


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    status: StageStatus
    summary_markdown: str
    risks: list[str] = field(default_factory=list)
    follow_up_tasks: list[str] = field(default_factory=list)
    partial_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "risks": self.risks,
            "follow_up_tasks": self.follow_up_tasks,
            "partial_reason": self.partial_reason,
        }


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    error: bool
    stage: str
    step: str
    code: str
    message: str
    task_id: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["error"] = True
        return payload
