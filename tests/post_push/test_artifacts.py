"""Tests for guardrails.post_push.artifacts: FileArtifactStore."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails import (  # noqa: E402
    FileArtifactStore,
    PipelineConfig,
    PostPushContext,
    WorkItemIdentity,
)


class FileArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.artifacts_base = self.repo_root / "artifacts_test_run"
        if self.artifacts_base.exists():
            shutil.rmtree(self.artifacts_base)

    def tearDown(self) -> None:
        if self.artifacts_base.exists():
            shutil.rmtree(self.artifacts_base)

    def _context(self, group_id: str = "g1", commit_sha: str = "abc", run_id: str = "r1") -> PostPushContext:
        return PostPushContext(
            task_id="TASK-001",
            repo="org/repo",
            branch="main",
            sha=commit_sha,
            identity=WorkItemIdentity(group_id=group_id, commit_sha=commit_sha, run_id=run_id),
        )

    def test_init_run_creates_directory_structure(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        config = PipelineConfig(artifacts_root="artifacts_test_run")
        context = self._context(group_id="pr-1", commit_sha="deadbeef", run_id="run-1")
        path = store.init_run(context=context, config=config)
        expected = self.repo_root / "artifacts_test_run" / "pr-1" / "deadbeef" / "run-1"
        self.assertEqual(path, expected)
        self.assertTrue(path.is_dir())

    def test_run_dir_after_init_returns_same_path(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        config = PipelineConfig(artifacts_root="artifacts_test_run")
        context = self._context()
        init_path = store.init_run(context=context, config=config)
        self.assertEqual(store.run_dir, init_path)

    def test_run_dir_before_init_raises_runtime_error(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        with self.assertRaises(RuntimeError) as ctx:
            _ = store.run_dir
        self.assertIn("not initialized", str(ctx.exception))
        self.assertIn("init_run", str(ctx.exception))

    def test_write_json_creates_file_with_indented_json(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        store.init_run(context=self._context(), config=PipelineConfig(artifacts_root="artifacts_test_run"))
        payload = {"key": "value", "nested": {"a": 1}}
        path = store.write_json("report.json", payload)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "report.json")
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertEqual(parsed, payload)
        self.assertIn("\n", raw)

    def test_write_json_creates_parent_directories(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        store.init_run(context=self._context(), config=PipelineConfig(artifacts_root="artifacts_test_run"))
        path = store.write_json("subdir/nested/report.json", {"x": 1})
        self.assertTrue(path.exists())
        self.assertTrue(path.parent.is_dir())
        self.assertEqual(path.parent.name, "nested")

    def test_write_text_creates_file_with_content(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        store.init_run(context=self._context(), config=PipelineConfig(artifacts_root="artifacts_test_run"))
        content = "# Markdown\n\nBody text."
        path = store.write_text("summary.md", content)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_write_text_creates_parent_directories(self) -> None:
        store = FileArtifactStore(repo_root=self.repo_root)
        store.init_run(context=self._context(), config=PipelineConfig(artifacts_root="artifacts_test_run"))
        path = store.write_text("logs/out.txt", "log line")
        self.assertTrue(path.exists())
        self.assertTrue((path.parent / "out.txt").exists() or path.name == "out.txt")


if __name__ == "__main__":
    unittest.main()
