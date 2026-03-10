from __future__ import annotations

from pathlib import Path
from typing import Protocol  

from .contracts import (
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    Finding,
    PipelineConfig,
    PostPushContext,
    SynthesisResult,
)
from .merge_decision import MergeDecisionResult


class CleanupAdapter(Protocol):
    def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
        ...


class ValidationAdapter(Protocol):
    def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        ...


class AuditAgent(Protocol):
    name: str

    def run(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        config: PipelineConfig,
    ) -> list[Finding]:
        ...


class DedupeStore(Protocol):
    def load_open_keys(self, group_id: str) -> set[str]:
        ...

    def save_open_keys(self, group_id: str, dedupe_keys: set[str]) -> None:
        ...

    def known_issue_ref(self, dedupe_key: str) -> str | None:
        ...


class SynthesisAdapter(Protocol):
    def synthesize(
        self,
        context: PostPushContext,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> SynthesisResult:
        ...


class MergeDecisionAdapter(Protocol):
    def decide(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> MergeDecisionResult:
        ...


class TaskTracker(Protocol):
    """Create/lookup external tasks for findings; e.g. Beads (bd)."""

    def load_refs(self) -> dict[str, str]:
        """Return dedupe_key -> task_id mapping (for known_issue_ref)."""
        ...

    def get_task_ref(self, dedupe_key: str) -> str | None:
        """Return existing task id for this fingerprint, or None."""
        ...

    def create_task_for_finding(
        self,
        finding: Finding,
        priority: str | int = 2,
        task_type: str = "task",
    ) -> str | None:
        """Create a task for this finding if none exists; return task id or None."""
        ...


class ArtifactStore(Protocol):
    def init_run(self, context: PostPushContext, config: PipelineConfig) -> Path:
        ...

    def write_json(self, relative_path: str, payload: dict) -> Path:
        ...

    def write_text(self, relative_path: str, content: str) -> Path:
        ...

    @property
    def run_dir(self) -> Path:
        ...
