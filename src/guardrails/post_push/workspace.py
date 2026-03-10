"""Workspace path convention shared with Pre-Execution (Group 1).

Pre-Execution creates the workspace; Post-Push cleans it up. Both use the same paths.
Locate workspace via: repo_root / WORKSPACE_DIR_NAME / task_id.
"""

from __future__ import annotations

from pathlib import Path

from beartype import beartype

from .constants import (
    PRE_COMMIT_REPORT_NAME,
    RUNTIME_JSON_NAME,
    WORKSPACE_DIR_NAME,
)


@beartype
def workspace_path_for(repo_root: Path | str, task_id: str) -> Path:
    """Resolve the workspace directory path for a task.

    Parameters
    ----------
    repo_root
        Repository root (absolute path).
    task_id
        Canonical task identifier.

    Returns
    -------
    Path
        Absolute path to .agentic-workspaces/<task_id>/ (directory; may not exist).
    """
    root = Path(repo_root).resolve()
    return root / WORKSPACE_DIR_NAME / task_id


@beartype
def runtime_json_path(workspace_dir: Path) -> Path:
    """Path to runtime.json inside a workspace (Group 2 reads; does not modify)."""
    return workspace_dir / RUNTIME_JSON_NAME


@beartype
def pre_commit_report_path(workspace_dir: Path) -> Path:
    """Path to pre-commit-report.json (optional context for Post-Push)."""
    return workspace_dir / PRE_COMMIT_REPORT_NAME
