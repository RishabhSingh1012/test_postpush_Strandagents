"""Shared Click group for guardrails CLI.

Commands: pre-exec, pre-commit, pre-push, post-push.
Pre/post commands accept --task-id; post-push derives a deterministic fallback when omitted.
"""

from __future__ import annotations

import shutil
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

import click

from .post_push import (
    BeadsTaskTracker,
    FileBackedDedupeStore,
    FileArtifactStore,
    PostPushPipeline,
)
from .post_push.audit import StrandsAdversarialAgent, StrandsOptimizationAgent, StrandsReviewerAgent
from .post_push.cleanup import ManagedSafeCleanup
from .post_push.constants import (
    ARTIFACT_KEY_POST_PUSH_REPORT,
    ARTIFACTS_ROOT_DEFAULT,
    BEADS_REFS_FILENAME,
    GUARDRAILS_DIR_NAME,
    POST_PUSH_DEDUPE_FILENAME,
)
from .post_push.contracts import MutationMode, PipelineConfig, PostPushContext, WorkItemIdentity
from .post_push.synthesis import RepoIntrospectionSynthesis
from .post_push.validation import CommandValidationAdapter
from beartype import beartype


@beartype
def _repo_root() -> Path:
    """Repository root: git rev-parse --show-toplevel or cwd."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return Path.cwd()


@beartype
def _git(repo_root: Path, args: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@beartype
def _current_branch() -> str:
    """Current git branch or 'HEAD'."""
    repo_root = _repo_root()
    result = _git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "HEAD"


@beartype
def _resolve_sha(repo_root: Path, raw_sha: str | None) -> str:
    """Resolve a commit-ish to an immutable SHA."""
    commitish = raw_sha or "HEAD"
    result = _git(repo_root, ["rev-parse", commitish])
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return commitish


@beartype
def _default_base_ref(repo_root: Path) -> str:
    """Best-effort base branch for diff scope."""
    env_base = os.getenv("POST_PUSH_BASE_REF")
    if env_base:
        return env_base

    remote_head = _git(repo_root, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if remote_head.returncode == 0 and remote_head.stdout.strip():
        value = remote_head.stdout.strip()
        if value.startswith("refs/remotes/"):
            return value.removeprefix("refs/remotes/")

    for candidate in ("origin/main", "main", "origin/master", "master"):
        ref = f"refs/remotes/{candidate}" if candidate.startswith("origin/") else f"refs/heads/{candidate}"
        exists = _git(repo_root, ["show-ref", "--verify", ref])
        if exists.returncode == 0:
            return candidate
    return "HEAD"


@beartype
def _changed_files(repo_root: Path, commit_sha: str, pr: str | None) -> list[str]:
    """Diff-aware file scope for audit agents."""
    base_ref = f"{commit_sha}^"
    if pr:
        merge_base = _git(repo_root, ["merge-base", _default_base_ref(repo_root), commit_sha], timeout=10)
        if merge_base.returncode == 0 and merge_base.stdout.strip():
            base_ref = merge_base.stdout.strip()

    diff = _git(repo_root, ["diff", "--name-only", base_ref, commit_sha], timeout=10)
    if diff.returncode != 0:
        return []

    files: list[str] = []
    for line in diff.stdout.splitlines():
        path = line.strip()
        if not path:
            continue
        full = repo_root / path
        if full.is_file():
            files.append(path)
    return files


@beartype
def _default_run_id() -> str:
    """Unique run id: compact UTC timestamp + short nonce."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{token_hex(4)}"


@beartype
def _default_group_id(pr: str | None, branch: str) -> str:
    """group_id: pr-<n> when PR given, else branch-<name>."""
    if pr:
        digits = "".join(c for c in pr if c.isdigit())
        return f"pr-{digits}" if digits else f"pr-{pr[:20]}"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in branch)
    return f"branch-{safe or 'HEAD'}"


@beartype
def _default_task_id(
    explicit_group_id: str | None,
    pr: str | None,
    branch: str,
    commit_sha: str,
) -> str:
    """Deterministic fallback task id when --task-id is not provided."""
    if explicit_group_id:
        return explicit_group_id
    if pr:
        digits = "".join(c for c in pr if c.isdigit())
        if digits:
            return f"pr-{digits}"
        return f"pr-{pr[:20]}"
    safe_branch = "".join(c if c.isalnum() or c in "-_" else "_" for c in branch)
    if safe_branch:
        return f"branch-{safe_branch}"
    return f"sha-{commit_sha[:12]}"


def _require_bd_for_beads() -> None:
    """Fail fast when --beads is requested without Beads CLI (bd) in PATH."""
    if shutil.which("bd"):
        return
    raise click.ClickException(
        "Beads CLI ('bd') is required for --beads but was not found in PATH.\n"
        "Install Beads first, then run `bd init` in your project.\n"
        "Install options:\n"
        "  - macOS (Homebrew): brew install beads\n"
        "  - Linux/macOS: curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash"
    )


@click.group()
def cli() -> None:
    """Agentic development guardrails: pre-exec, pre-commit, pre-push, post-push."""


@cli.command("post-push")
@click.option(
    "--task-id",
    default=None,
    help="Canonical task identifier (default: derived from group/pr/branch/sha).",
)
@click.option(
    "--profile",
    type=click.Choice(["light", "full"], case_sensitive=False),
    default="light",
    show_default=True,
    help="Rigor level.",
)
@click.option("--pr", default=None, help="PR id or URL for traceability.")
@click.option("--sha", default=None, help="Target commit SHA (default: HEAD).")
@click.option("--group-id", default=None, help="Logical grouping key (default: pr-<n> or branch-<name>).")
@click.option("--run-id", default=None, help="Unique execution id (default: timestamp-nonce).")
@click.option("--runtime-id", default=None, help="Runtime/work item identifier for traceability.")
@click.option("--branch", default=None, help="Branch name (default: current branch).")
@click.option(
    "--cleanup",
    type=click.Choice(["safe", "off"], case_sensitive=False),
    default=None,
    help="Local cleanup mode (profile default when omitted).",
)
@click.option(
    "--mutation",
    type=click.Choice(["off", "sample", "full"], case_sensitive=False),
    default=None,
    help="Mutation testing mode (profile default when omitted).",
)
@click.option(
    "--artifacts-dir",
    default=ARTIFACTS_ROOT_DEFAULT,
    show_default=True,
    help="Artifacts root directory under repo.",
)
@click.option(
    "--beads",
    is_flag=True,
    default=False,
    help=f"Create/lookup Beads (bd) tasks for findings; persist refs in {GUARDRAILS_DIR_NAME}/{BEADS_REFS_FILENAME}.",
)
@beartype
def post_push(
    task_id: str | None,
    profile: str,
    pr: str | None,
    sha: str | None,
    group_id: str | None,
    run_id: str | None,
    runtime_id: str | None,
    branch: str | None,
    cleanup: str | None,
    mutation: str | None,
    artifacts_dir: str,
    beads: bool,
) -> None:
    """Run post-push: safe cleanup, deep validation, audit pipeline, current-state synthesis."""
    if beads:
        _require_bd_for_beads()

    repo_root = _repo_root()
    branch_name = branch or _current_branch()
    commit_sha = _resolve_sha(repo_root, sha)
    run_id_val = run_id or _default_run_id()
    group_id_val = group_id or _default_group_id(pr, branch_name)
    task_id_val = task_id or _default_task_id(
        explicit_group_id=group_id,
        pr=pr,
        branch=branch_name,
        commit_sha=commit_sha,
    )
    changed_files = _changed_files(repo_root, commit_sha=commit_sha, pr=pr)

    identity = WorkItemIdentity(
        group_id=group_id_val,
        commit_sha=commit_sha,
        run_id=run_id_val,
    )
    context = PostPushContext(
        task_id=task_id_val,
        repo=str(repo_root),
        branch=branch_name,
        sha=commit_sha,
        identity=identity,
        profile=profile,
        pr=pr,
        changed_files=changed_files,
        runtime_id=runtime_id,
    )
    cleanup_mode = cleanup.lower() if cleanup else "safe"
    profile_norm = profile.lower()
    mutation_name = mutation.lower() if mutation else ("sample" if profile_norm == "full" else "off")
    mutation_mode = getattr(MutationMode, mutation_name.upper(), MutationMode.OFF)
    config = PipelineConfig(
        cleanup_mode=cleanup_mode,
        mutation_mode=mutation_mode,
        run_audits_on_validation_failure=True,
        artifacts_root=artifacts_dir,
    )

    task_tracker: BeadsTaskTracker | None = None
    known_issues: dict[str, str] = {}
    if beads:
        task_tracker = BeadsTaskTracker(repo_root=repo_root)
        known_issues = task_tracker.load_refs()

    pipeline = PostPushPipeline(
        cleanup=ManagedSafeCleanup(repo_root=repo_root),
        validation=CommandValidationAdapter(repo_root=repo_root),
        audit_agents=[
            StrandsAdversarialAgent(),
            StrandsOptimizationAgent(),
            StrandsReviewerAgent(),
        ],
        synthesis=RepoIntrospectionSynthesis(),
        artifact_store=FileArtifactStore(repo_root=repo_root),
        dedupe_store=FileBackedDedupeStore(
            path=repo_root / GUARDRAILS_DIR_NAME / POST_PUSH_DEDUPE_FILENAME,
            known_issues=known_issues,
        ),
        task_tracker=task_tracker,
    )
    outcome = pipeline.run(context=context, config=config)

    click.echo(f"Result: {outcome.report['overall_result'].upper()}")
    click.echo(f"Report: {outcome.report['artifacts'].get(ARTIFACT_KEY_POST_PUSH_REPORT, '')}")
    raise SystemExit(outcome.exit_code)


@beartype
def main() -> None:
    """Entrypoint for the guardrails CLI."""
    cli()
