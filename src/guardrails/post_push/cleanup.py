from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from beartype import beartype

from .constants import AGENTIC_BRANCH_PREFIX, WORKSPACE_DIR_NAME
from .contracts import CleanupResult, CleanupSkip, PipelineConfig, PostPushContext, StageStatus
from .workspace import runtime_json_path

_GIT_TIMEOUT_SECONDS = 10
_BASE_BRANCH_CANDIDATES = ("origin/main", "main", "origin/master", "master")


class ManagedSafeCleanup:
    __slots__ = ("_repo_root",)

    @beartype
    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root = repo_root

    @beartype
    def cleanup(self, context: PostPushContext, config: PipelineConfig) -> CleanupResult:
        cleaned: list[str] = []
        skipped: list[CleanupSkip] = []
        policy_violations: list[str] = []

        if config.cleanup_mode.lower() == "off":
            skipped.append(CleanupSkip(item="cleanup", reason="cleanup_mode_off"))
            return CleanupResult(
                status=StageStatus.PARTIAL,
                cleaned=cleaned,
                skipped=skipped,
                policy_violations=policy_violations,
            )

        repo_root = self._resolve_repo_root(context)
        workspace = context.workspace_path

        runtime_payload = self._load_runtime(workspace)
        if runtime_payload is None:
            skipped.append(CleanupSkip(item=str(runtime_json_path(workspace)), reason="runtime_metadata_missing"))

        branch_name = self._resolve_branch_name(context=context, runtime_payload=runtime_payload)
        branch_has_unmerged_commits = self._precheck_branch_unmerged_commits(
            context=context,
            branch_name=branch_name,
            skipped=skipped,
        )

        self._cleanup_workspace(
            context=context,
            repo_root=repo_root,
            workspace=workspace,
            skip_due_to_unmerged=branch_has_unmerged_commits,
            cleaned=cleaned,
            skipped=skipped,
            policy_violations=policy_violations,
        )

        self._cleanup_branch(
            context=context,
            branch_name=branch_name,
            prechecked_unmerged=branch_has_unmerged_commits,
            cleaned=cleaned,
            skipped=skipped,
            policy_violations=policy_violations,
        )

        status = _status_for_cleanup(skipped=skipped, policy_violations=policy_violations)
        return CleanupResult(
            status=status,
            cleaned=cleaned,
            skipped=skipped,
            policy_violations=policy_violations,
        )

    def _resolve_repo_root(self, context: PostPushContext) -> Path:
        if self._repo_root is not None:
            return self._repo_root
        return Path(context.repo).resolve()

    def _load_runtime(self, workspace: Path) -> dict | None:
        runtime_path = runtime_json_path(workspace)
        if not runtime_path.is_file():
            return None
        try:
            raw = runtime_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _cleanup_workspace(
        self,
        context: PostPushContext,
        repo_root: Path,
        workspace: Path,
        skip_due_to_unmerged: bool,
        cleaned: list[str],
        skipped: list[CleanupSkip],
        policy_violations: list[str],
    ) -> None:
        if skip_due_to_unmerged:
            skipped.append(CleanupSkip(item=str(workspace), reason="unmerged_local_commits"))
            return

        managed_root = repo_root / WORKSPACE_DIR_NAME
        workspace_resolved = workspace.resolve()
        if not workspace_resolved.is_relative_to(managed_root.resolve()):
            skipped.append(CleanupSkip(item=str(workspace), reason="not_harness_managed"))
            policy_violations.append(f"Workspace outside managed root: {workspace}")
            return

        if not workspace.exists():
            skipped.append(CleanupSkip(item=str(workspace), reason="workspace_missing"))
            return

        try:
            if self._is_registered_worktree(context=context, workspace=workspace):
                self._run_git(context, ["worktree", "remove", "--force", str(workspace)])
            if workspace.exists():
                shutil.rmtree(workspace)
            cleaned.append(str(workspace))
        except Exception as exc:  # pragma: no cover - defensive path
            policy_violations.append(f"Failed to cleanup workspace {workspace}: {exc}")

    def _cleanup_branch(
        self,
        context: PostPushContext,
        branch_name: str | None,
        prechecked_unmerged: bool,
        cleaned: list[str],
        skipped: list[CleanupSkip],
        policy_violations: list[str],
    ) -> None:
        if prechecked_unmerged:
            return

        if not branch_name:
            skipped.append(CleanupSkip(item="branch", reason="runtime_metadata_missing"))
            return

        if not branch_name.startswith(AGENTIC_BRANCH_PREFIX):
            skipped.append(CleanupSkip(item=branch_name, reason="not_harness_managed"))
            return

        if not self._branch_exists(context, branch_name):
            skipped.append(CleanupSkip(item=branch_name, reason="branch_not_found"))
            return

        if self._has_unmerged_local_commits(context, branch_name):
            skipped.append(CleanupSkip(item=branch_name, reason="unmerged_local_commits"))
            return

        try:
            self._run_git(context, ["branch", "-d", branch_name])
            cleaned.append(f"branch:{branch_name}")
        except RuntimeError as exc:
            policy_violations.append(str(exc))

    def _precheck_branch_unmerged_commits(
        self,
        context: PostPushContext,
        branch_name: str | None,
        skipped: list[CleanupSkip],
    ) -> bool:
        if not branch_name:
            return False
        if not branch_name.startswith(AGENTIC_BRANCH_PREFIX):
            return False
        if not self._branch_exists(context, branch_name):
            return False
        if not self._has_unmerged_local_commits(context, branch_name):
            return False
        skipped.append(CleanupSkip(item=branch_name, reason="unmerged_local_commits"))
        return True

    def _resolve_branch_name(self, context: PostPushContext, runtime_payload: dict | None) -> str | None:
        if runtime_payload and isinstance(runtime_payload.get("workspace"), dict):
            branch = runtime_payload["workspace"].get("branch")
            if isinstance(branch, str) and branch.strip():
                return branch.strip()
        if context.branch.startswith(AGENTIC_BRANCH_PREFIX):
            return context.branch
        return None

    def _branch_exists(self, context: PostPushContext, branch_name: str) -> bool:
        result = self._run_git(context, ["show-ref", "--verify", f"refs/heads/{branch_name}"], check=False)
        return result.returncode == 0

    def _has_unmerged_local_commits(self, context: PostPushContext, branch_name: str) -> bool:
        base_ref = self._base_ref(context)
        if base_ref is None:
            return False
        result = self._run_git(context, ["rev-list", "--count", f"{base_ref}..{branch_name}"], check=False)
        if result.returncode != 0:
            return False
        try:
            ahead_count = int(result.stdout.strip() or "0")
        except ValueError:
            return False
        return ahead_count > 0

    def _base_ref(self, context: PostPushContext) -> str | None:
        for candidate in _BASE_BRANCH_CANDIDATES:
            if candidate.startswith("origin/"):
                ref = f"refs/remotes/{candidate}"
            else:
                ref = f"refs/heads/{candidate}"
            result = self._run_git(context, ["show-ref", "--verify", ref], check=False)
            if result.returncode == 0:
                return candidate
        return None

    def _is_registered_worktree(self, context: PostPushContext, workspace: Path) -> bool:
        result = self._run_git(context, ["worktree", "list", "--porcelain"], check=False)
        if result.returncode != 0:
            return False
        workspace_norm = str(workspace.resolve())
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                listed = line.removeprefix("worktree ").strip()
                if listed and str(Path(listed).resolve()) == workspace_norm:
                    return True
        return False

    def _run_git(
        self,
        context: PostPushContext,
        args: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git"] + args,
            cwd=context.repo,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise RuntimeError(f"git {' '.join(args)} failed: {message}")
        return result


def _status_for_cleanup(skipped: list[CleanupSkip], policy_violations: list[str]) -> StageStatus:
    if policy_violations:
        return StageStatus.FAIL
    if skipped:
        return StageStatus.PARTIAL
    return StageStatus.PASS
