"""Tests for post-push CLI profile defaults and runtime-id propagation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guardrails.cli import cli  # noqa: E402
from guardrails.post_push.contracts import MutationMode  # noqa: E402


class _Captured:
    context = None
    config = None


class _FakePipeline:
    def __init__(self, **kwargs) -> None:
        _ = kwargs

    def run(self, context, config):
        _Captured.context = context
        _Captured.config = config
        return type(
            "Outcome",
            (),
            {
                "exit_code": 0,
                "report": {"overall_result": "pass", "artifacts": {"post_push_report": "/tmp/report.json"}},
            },
        )()


class CliPostPushTests(unittest.TestCase):
    def setUp(self) -> None:
        _Captured.context = None
        _Captured.config = None

    def _invoke(self, args: list[str]):
        runner = CliRunner()
        with (
            patch("guardrails.cli.PostPushPipeline", _FakePipeline),
            patch("guardrails.cli._repo_root", return_value=Path.cwd()),
            patch("guardrails.cli._current_branch", return_value="main"),
            patch("guardrails.cli._resolve_sha", return_value="abc123"),
            patch("guardrails.cli._changed_files", return_value=["src/guardrails/cli.py"]),
        ):
            return runner.invoke(cli, args)

    def test_full_profile_defaults_mutation_to_sample(self) -> None:
        result = self._invoke(["post-push", "--task-id", "TASK-001", "--profile", "full"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(_Captured.config.mutation_mode, MutationMode.SAMPLE)

    def test_light_profile_defaults_mutation_to_off(self) -> None:
        result = self._invoke(["post-push", "--task-id", "TASK-001", "--profile", "light"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(_Captured.config.mutation_mode, MutationMode.OFF)

    def test_runtime_id_is_forwarded_to_context(self) -> None:
        result = self._invoke(
            [
                "post-push",
                "--task-id",
                "TASK-001",
                "--runtime-id",
                "RT-123",
            ]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(_Captured.context.runtime_id, "RT-123")

    def test_task_id_is_derived_from_group_id_when_omitted(self) -> None:
        result = self._invoke(
            [
                "post-push",
                "--group-id",
                "pr-482",
            ]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(_Captured.context.task_id, "pr-482")

    def test_task_id_is_derived_from_branch_when_omitted(self) -> None:
        result = self._invoke(["post-push", "--branch", "feature/demo"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(_Captured.context.task_id, "branch-feature_demo")


if __name__ == "__main__":
    unittest.main()
