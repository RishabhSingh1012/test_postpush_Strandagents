from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from beartype import beartype

from .contracts import (
    DeepValidationResult,
    MutationMode,
    PipelineConfig,
    PostPushContext,
    StageStatus,
    ValidationCheck,
)

_DEFAULT_TEST_CMD = "pytest -q"
_DEFAULT_BUILD_CMD = "python -m compileall -q src"
_DEFAULT_TYPECHECK_CMD = "python -m mypy src"
_DEFAULT_MUTATION_TIMEOUT_SAMPLE_SEC = 20 * 60
_DEFAULT_MUTATION_TIMEOUT_FULL_SEC = 60 * 60
_DEFAULT_COMMAND_TIMEOUT_SEC = 20 * 60
_DEFAULT_MUTATION_WORKERS = 4
_DEFAULT_MUTATION_THRESHOLD = 70
_MAX_OUTPUT_SUMMARY = 400


@dataclass(frozen=True, slots=True)
class _CommandResult:
    status: StageStatus
    details: str


class CommandValidationAdapter:
    __slots__ = (
        "_repo_root",
        "_unit_test_cmd",
        "_integration_test_cmd",
        "_build_cmd",
        "_typecheck_cmd",
        "_mutation_cmd",
        "_mutation_targets",
        "_command_timeout_sec",
        "_mutation_timeout_sample_sec",
        "_mutation_timeout_full_sec",
        "_mutation_workers",
        "_mutation_threshold",
    )

    @beartype
    def __init__(
        self,
        repo_root: Path | None = None,
        unit_test_cmd: str | None = None,
        integration_test_cmd: str | None = None,
        build_cmd: str | None = None,
        typecheck_cmd: str | None = None,
        mutation_cmd: str | None = None,
        mutation_targets: list[str] | None = None,
        command_timeout_sec: int = _DEFAULT_COMMAND_TIMEOUT_SEC,
        mutation_timeout_sample_sec: int = _DEFAULT_MUTATION_TIMEOUT_SAMPLE_SEC,
        mutation_timeout_full_sec: int = _DEFAULT_MUTATION_TIMEOUT_FULL_SEC,
        mutation_workers: int = _DEFAULT_MUTATION_WORKERS,
        mutation_threshold: int = _DEFAULT_MUTATION_THRESHOLD,
    ) -> None:
        self._repo_root = repo_root
        self._unit_test_cmd = unit_test_cmd or os.getenv("POST_PUSH_UNIT_TEST_CMD") or os.getenv("POST_PUSH_TEST_CMD") or _DEFAULT_TEST_CMD
        self._integration_test_cmd = integration_test_cmd or os.getenv("POST_PUSH_INTEGRATION_TEST_CMD") or self._unit_test_cmd
        self._build_cmd = build_cmd or os.getenv("POST_PUSH_BUILD_CMD") or _DEFAULT_BUILD_CMD
        self._typecheck_cmd = typecheck_cmd or os.getenv("POST_PUSH_TYPECHECK_CMD") or _DEFAULT_TYPECHECK_CMD
        self._mutation_cmd = mutation_cmd or os.getenv("POST_PUSH_MUTATION_CMD")
        env_targets = os.getenv("POST_PUSH_MUTATION_TARGETS", "")
        self._mutation_targets = mutation_targets or [item.strip() for item in env_targets.split(",") if item.strip()] or ["src"]
        self._command_timeout_sec = command_timeout_sec
        self._mutation_timeout_sample_sec = mutation_timeout_sample_sec
        self._mutation_timeout_full_sec = mutation_timeout_full_sec
        self._mutation_workers = mutation_workers
        self._mutation_threshold = mutation_threshold

    @beartype
    def validate(self, context: PostPushContext, config: PipelineConfig) -> DeepValidationResult:
        checks: list[ValidationCheck] = []
        blocking_failures: list[str] = []
        non_blocking_notes: list[str] = []

        for check_name, command in (
            ("unit-tests", self._unit_test_cmd),
            ("integration-tests", self._integration_test_cmd),
            ("build", self._build_cmd),
            ("type-check", self._typecheck_cmd),
        ):
            result = self._run_command(
                command=command,
                cwd=self._repo_root or Path(context.repo),
                timeout_sec=self._command_timeout_sec,
            )
            checks.append(
                ValidationCheck(
                    name=check_name,
                    status=result.status,
                    blocking=True,
                    details=result.details,
                )
            )
            if result.status == StageStatus.FAIL:
                blocking_failures.append(check_name)

        mutation_score: int | None = None
        mutation_threshold: int | None = self._mutation_threshold
        mutation_mode = config.mutation_mode if isinstance(config.mutation_mode, MutationMode) else MutationMode.OFF
        mutation_status = StageStatus.SKIP
        mutation_details = "Mutation disabled."
        mutation_blocking = context.profile.lower() == "full"

        if mutation_mode != MutationMode.OFF:
            mutation_status, mutation_details, mutation_score, mutation_issue = self._run_mutation(
                context=context,
                mutation_mode=mutation_mode,
            )
            if mutation_status == StageStatus.FAIL and mutation_issue:
                if mutation_blocking:
                    blocking_failures.append(mutation_issue)
                else:
                    non_blocking_notes.append(mutation_issue)
            elif mutation_score is not None and mutation_score < self._mutation_threshold:
                note = f"mutation-threshold-miss ({mutation_score} < {self._mutation_threshold})"
                if mutation_blocking:
                    blocking_failures.append(note)
                else:
                    non_blocking_notes.append(note)
            checks.append(
                ValidationCheck(
                    name="mutation",
                    status=mutation_status,
                    blocking=mutation_blocking,
                    details=mutation_details,
                )
            )

        status = StageStatus.FAIL if blocking_failures else StageStatus.PASS
        return DeepValidationResult(
            status=status,
            checks=checks,
            blocking_failures=blocking_failures,
            non_blocking_notes=non_blocking_notes,
            mutation_mode=mutation_mode,
            mutation_score=mutation_score,
            mutation_threshold=mutation_threshold,
        )

    def _run_mutation(
        self,
        context: PostPushContext,
        mutation_mode: MutationMode,
    ) -> tuple[StageStatus, str, int | None, str | None]:
        if not self._mutation_cmd:
            return StageStatus.FAIL, "Mutation command not configured.", None, "mutation-command-missing"

        if mutation_mode == MutationMode.SAMPLE:
            targets = self._mutation_targets[: min(3, len(self._mutation_targets))]
            timeout_sec = self._mutation_timeout_sample_sec
        else:
            targets = self._mutation_targets
            timeout_sec = self._mutation_timeout_full_sec

        rendered_cmd = self._render_mutation_command(mutation_mode=mutation_mode, targets=targets)
        result = self._run_command(
            command=rendered_cmd,
            cwd=self._repo_root or Path(context.repo),
            timeout_sec=timeout_sec,
        )
        score = _extract_mutation_score(result.details)
        if result.status == StageStatus.FAIL:
            return StageStatus.FAIL, result.details, score, "mutation-command-failed"
        return StageStatus.PASS, result.details, score, None

    def _render_mutation_command(self, mutation_mode: MutationMode, targets: list[str]) -> str:
        rendered = self._mutation_cmd or ""
        target_str = ",".join(targets)
        rendered = rendered.replace("{mode}", mutation_mode.value)
        rendered = rendered.replace("{targets}", target_str)
        rendered = rendered.replace("{workers}", str(self._mutation_workers))
        return rendered

    def _run_command(self, command: str, cwd: Path, timeout_sec: int) -> _CommandResult:
        if not command.strip():
            return _CommandResult(status=StageStatus.FAIL, details="Command not configured.")

        try:
            result = subprocess.run(
                shlex.split(command),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except FileNotFoundError:
            return _CommandResult(status=StageStatus.FAIL, details=f"Command not found: {command}")
        except subprocess.TimeoutExpired:
            return _CommandResult(status=StageStatus.FAIL, details=f"Timeout after {timeout_sec}s: {command}")

        details = _summarize_output(command=command, stdout=result.stdout, stderr=result.stderr, code=result.returncode)
        status = StageStatus.PASS if result.returncode == 0 else StageStatus.FAIL
        return _CommandResult(status=status, details=details)


def _summarize_output(command: str, stdout: str, stderr: str, code: int) -> str:
    source = (stderr or stdout or "").strip()
    compact = re.sub(r"\s+", " ", source)
    if len(compact) > _MAX_OUTPUT_SUMMARY:
        compact = compact[:_MAX_OUTPUT_SUMMARY].rstrip() + "..."
    if not compact:
        compact = "no output"
    return f"exit={code}; cmd='{command}'; output={compact}"


def _extract_mutation_score(details: str) -> int | None:
    match = re.search(r"(?:mutation[_ -]?score|score)\D+(\d{1,3})", details, re.IGNORECASE)
    if not match:
        return None
    score = int(match.group(1))
    if score < 0:
        return 0
    if score > 100:
        return 100
    return score
