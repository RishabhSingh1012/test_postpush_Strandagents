"""Tests for guardrails.post_push.synthesis: RepoIntrospectionSynthesis."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.contracts import (  # noqa: E402
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    PipelineConfig,
    PostPushContext,
    StageStatus,
    WorkItemIdentity,
)
from guardrails.post_push.synthesis import RepoIntrospectionSynthesis  # noqa: E402


class RepoIntrospectionSynthesisTests(unittest.TestCase):
    def test_synthesis_contains_current_state_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src" / "guardrails").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "guardrails" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "src" / "guardrails" / "cli.py").write_text('@cli.command("post-push")\n', encoding="utf-8")
            (repo / "tests").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text(
                '[project]\nname="demo"\n[project.scripts]\nguardrails="guardrails.cli:main"\n',
                encoding="utf-8",
            )
            (repo / "README.md").write_text("### 5. Post-Push\n", encoding="utf-8")

            context = PostPushContext(
                task_id="TASK-001",
                repo=str(repo),
                branch="main",
                sha="abc123",
                identity=WorkItemIdentity(group_id="g1", commit_sha="abc123", run_id="r1"),
                profile="full",
            )
            result = RepoIntrospectionSynthesis().synthesize(
                context=context,
                cleanup=CleanupResult(status=StageStatus.PASS),
                validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                audit=AuditResult(status=StageStatus.PASS),
                config=PipelineConfig(),
            )

            self.assertIn("## Repository", result.summary_markdown)
            self.assertIn("## Main Modules/Packages", result.summary_markdown)
            self.assertIn("## Public Interfaces", result.summary_markdown)
            self.assertIn("## Test Posture", result.summary_markdown)
            self.assertIn("guardrails -> guardrails.cli:main", result.summary_markdown)
            self.assertEqual(result.status, StageStatus.PASS)

    def test_synthesis_partial_when_repo_missing(self) -> None:
        context = PostPushContext(
            task_id="TASK-001",
            repo="/tmp/does-not-exist-guardrails-synthesis",
            branch="main",
            sha="abc123",
            identity=WorkItemIdentity(group_id="g1", commit_sha="abc123", run_id="r1"),
            profile="light",
        )
        result = RepoIntrospectionSynthesis().synthesize(
            context=context,
            cleanup=CleanupResult(status=StageStatus.PASS),
            validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
            audit=AuditResult(status=StageStatus.PASS),
            config=PipelineConfig(),
        )
        self.assertEqual(result.status, StageStatus.PARTIAL)
        self.assertIsNotNone(result.partial_reason)

    def test_synthesis_includes_conditional_sections_when_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src" / "guardrails").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "guardrails" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
            (repo / "frontend").mkdir(parents=True, exist_ok=True)
            (repo / "frontend" / "App.jsx").write_text('const r = { path: "/home" };', encoding="utf-8")
            (repo / "migrations").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "models.py").write_text("class User(Base):\n    pass\n", encoding="utf-8")
            (repo / "eval").mkdir(parents=True, exist_ok=True)
            (repo / "eval" / "benchmark_eval.py").write_text("accuracy = 0.9\n", encoding="utf-8")

            context = PostPushContext(
                task_id="TASK-001",
                repo=str(repo),
                branch="main",
                sha="abc123",
                identity=WorkItemIdentity(group_id="g1", commit_sha="abc123", run_id="r1"),
                profile="full",
            )
            result = RepoIntrospectionSynthesis().synthesize(
                context=context,
                cleanup=CleanupResult(status=StageStatus.PASS),
                validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                audit=AuditResult(status=StageStatus.PASS),
                config=PipelineConfig(),
            )
            self.assertEqual(result.status, StageStatus.PASS)
            self.assertIn("## UI Surfaces", result.summary_markdown)
            self.assertIn("## Database Structures", result.summary_markdown)
            self.assertIn("## AI / ML Evaluation Interfaces", result.summary_markdown)

    def test_synthesis_marks_partial_when_conditional_section_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "frontend").mkdir(parents=True, exist_ok=True)
            (repo / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")

            context = PostPushContext(
                task_id="TASK-001",
                repo=str(repo),
                branch="main",
                sha="abc123",
                identity=WorkItemIdentity(group_id="g1", commit_sha="abc123", run_id="r1"),
                profile="light",
            )
            result = RepoIntrospectionSynthesis().synthesize(
                context=context,
                cleanup=CleanupResult(status=StageStatus.PASS),
                validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                audit=AuditResult(status=StageStatus.PASS),
                config=PipelineConfig(),
            )
            self.assertEqual(result.status, StageStatus.PARTIAL)
            self.assertIn("UI section applicable but unavailable", result.partial_reason or "")


if __name__ == "__main__":
    unittest.main()
