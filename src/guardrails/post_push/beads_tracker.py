"""Beads task tracker: create and resolve tasks for findings via the Beads CLI (bd).

Stores fingerprint (dedupe_key) -> Beads issue ID in a repo-level JSON file so we
avoid creating duplicate tasks and can reference existing issues (known_issue_ref).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from beartype import beartype

from .constants import BEADS_CLI_COMMAND, BEADS_REFS_FILENAME, GUARDRAILS_DIR_NAME
from .contracts import Finding

# Beads priority: 0 highest, 3 lowest (matches P0-P3)
_PRIORITY_STR_TO_INT: dict[str, int] = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
    "P3": 3,
}


class BeadsTaskTracker:
    """Create and look up Beads (bd) tasks for audit findings; persist dedupe_key -> bd-id."""

    __slots__ = ("_repo_root", "_refs_file", "_bd_command", "_cwd", "_refs_cache")

    @beartype
    def __init__(
        self,
        repo_root: Path,
        refs_file: Path | None = None,
        bd_command: str = BEADS_CLI_COMMAND,
        cwd: Path | None = None,
    ) -> None:
        """Initialize tracker.

        Parameters
        ----------
        repo_root
            Repository root; refs file lives under repo_root / .guardrails / beads-task-refs.json
            unless refs_file is set.
        refs_file
            Override path for the JSON file that stores dedupe_key -> bd-id. Default:
            repo_root / .guardrails / beads-task-refs.json.
        bd_command
            Beads CLI command (default "bd").
        cwd
            Working directory when invoking bd (default repo_root). Must be a repo where
            `bd init` has been run.
        """
        self._repo_root = Path(repo_root).resolve()
        self._refs_file = Path(refs_file) if refs_file is not None else self._repo_root / GUARDRAILS_DIR_NAME / BEADS_REFS_FILENAME
        self._bd_command = bd_command
        self._cwd = Path(cwd).resolve() if cwd is not None else self._repo_root
        self._refs_cache: dict[str, str] | None = None

    @property
    def refs_file(self) -> Path:
        """Path to the JSON file storing dedupe_key -> bd-id."""
        return self._refs_file

    @beartype
    def load_refs(self) -> dict[str, str]:
        """Load dedupe_key -> bd-id mapping from disk. Returns empty dict if file missing."""
        if self._refs_cache is not None:
            return dict(self._refs_cache)
        if not self._refs_file.is_file():
            return {}
        try:
            data = json.loads(self._refs_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        out = {k: str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        self._refs_cache = out
        return out

    @beartype
    def get_task_ref(self, dedupe_key: str) -> str | None:
        """Return Beads issue ID for this fingerprint if one exists, else None."""
        refs = self.load_refs()
        return refs.get(dedupe_key)

    def _ensure_refs_dir(self) -> None:
        """Create parent directory of refs file if needed."""
        self._refs_file.parent.mkdir(parents=True, exist_ok=True)

    def _save_refs(self, refs: dict[str, str]) -> None:
        """Write refs to disk and update cache."""
        self._ensure_refs_dir()
        self._refs_file.write_text(json.dumps(refs, indent=2) + "\n", encoding="utf-8")
        self._refs_cache = dict(refs)

    def _run_bd(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Run bd with given args; cwd = self._cwd. Caller should check returncode."""
        cmd = [self._bd_command] + args
        try:
            return subprocess.run(
                cmd,
                cwd=self._cwd,
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as _:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout=b"",
                stderr=b"",
            )

    @beartype
    def create_task_for_finding(
        self,
        finding: Finding,
        priority: str | int = 2,
        task_type: str = "task",
    ) -> str | None:
        """Create a Beads task for this finding if none exists; return bd-id or None.

        If get_task_ref(finding.dedupe_key) is already set, returns that id without
        calling bd. Otherwise runs `bd create` with title/description from the finding,
        then stores dedupe_key -> id in the refs file and returns the id.

        Parameters
        ----------
        finding
            Audit finding (title, recommendation, evidence, dedupe_key).
        priority
            Beads priority 0-3 (P0=0, P1=1, P2=2, P3=3), or string "P0"/"P1"/"P2"/"P3".
        task_type
            Beads issue type (default "task").

        Returns
        -------
        str | None
            Beads issue ID (e.g. "bd-42") or None if creation failed or bd unavailable.
        """
        existing = self.get_task_ref(finding.dedupe_key)
        if existing is not None:
            return existing

        if isinstance(priority, str):
            priority_int = _PRIORITY_STR_TO_INT.get(priority.upper(), 2)
        else:
            priority_int = max(0, min(3, int(priority)))

        title = (finding.title or finding.finding_id).strip() or finding.finding_id
        evidence = ", ".join(finding.evidence[:3]) if finding.evidence else "not specified"
        description = f"What: {finding.recommendation}\nWhere: {evidence}"

        args = [
            "create",
            title,
            "--description",
            description,
            "-t",
            task_type,
            "-p",
            str(priority_int),
            "--json",
        ]
        result = self._run_bd(args)
        if result.returncode != 0:
            return None
        try:
            raw = result.stdout.decode("utf-8").strip()
            if not raw:
                return None
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        issue_id = data.get("id") or data.get("issue_id")
        if not issue_id:
            return None
        issue_id_str = str(issue_id)
        refs = self.load_refs()
        refs[finding.dedupe_key] = issue_id_str
        self._save_refs(refs)
        return issue_id_str
