from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.cleanup import ManagedSafeCleanup  # noqa: E402
from guardrails.post_push.contracts import PipelineConfig, PostPushContext, WorkItemIdentity  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


class ManagedSafeCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _run(["git", "init"], cwd=self.repo)
        _run(["git", "config", "user.name", "test"], cwd=self.repo)
        _run(["git", "config", "user.email", "test@example.com"], cwd=self.repo)
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        _run(["git", "add", "README.md"], cwd=self.repo)
        _run(["git", "commit", "-m", "init"], cwd=self.repo)
        _run(["git", "branch", "-M", "main"], cwd=self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _context(self, branch: str = "main") -> PostPushContext:
        return PostPushContext(
            task_id="TASK-001",
            repo=str(self.repo),
            branch=branch,
            sha="HEAD",
            identity=WorkItemIdentity(group_id="pr-1", commit_sha="head", run_id="r1"),
            profile="full",
        )

    def _branch_exists(self, name: str) -> bool:
        result = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{name}"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def test_cleanup_mode_off_returns_partial_with_reason(self) -> None:
        result = ManagedSafeCleanup(repo_root=self.repo).cleanup(
            context=self._context(),
            config=PipelineConfig(cleanup_mode="off"),
        )
        self.assertEqual(result.status.value, "partial")
        self.assertEqual(result.cleaned, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0].reason, "cleanup_mode_off")

    def test_cleanup_removes_managed_workspace_and_branch(self) -> None:
        _run(["git", "checkout", "-b", "agentic/TASK-001-demo"], cwd=self.repo)
        _run(["git", "checkout", "main"], cwd=self.repo)

        ws = self.repo / ".agentic-workspaces" / "TASK-001"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "runtime.json").write_text(
            json.dumps({"workspace": {"branch": "agentic/TASK-001-demo"}}),
            encoding="utf-8",
        )

        result = ManagedSafeCleanup(repo_root=self.repo).cleanup(
            context=self._context(),
            config=PipelineConfig(cleanup_mode="safe"),
        )
        self.assertEqual(result.status.value, "pass")
        self.assertFalse(ws.exists())
        self.assertFalse(self._branch_exists("agentic/TASK-001-demo"))
        self.assertGreaterEqual(len(result.cleaned), 2)

    def test_cleanup_skips_branch_with_unmerged_commits(self) -> None:
        _run(["git", "checkout", "-b", "agentic/TASK-001-demo"], cwd=self.repo)
        (self.repo / "README.md").write_text("ahead\n", encoding="utf-8")
        _run(["git", "add", "README.md"], cwd=self.repo)
        _run(["git", "commit", "-m", "ahead"], cwd=self.repo)
        _run(["git", "checkout", "main"], cwd=self.repo)

        ws = self.repo / ".agentic-workspaces" / "TASK-001"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "runtime.json").write_text(
            json.dumps({"workspace": {"branch": "agentic/TASK-001-demo"}}),
            encoding="utf-8",
        )

        result = ManagedSafeCleanup(repo_root=self.repo).cleanup(
            context=self._context(),
            config=PipelineConfig(cleanup_mode="safe"),
        )
        reasons = {item.reason for item in result.skipped}
        self.assertIn("unmerged_local_commits", reasons)
        self.assertTrue(ws.exists(), "workspace should not be removed when branch has unmerged commits")
        self.assertTrue(self._branch_exists("agentic/TASK-001-demo"))


if __name__ == "__main__":
    unittest.main()
