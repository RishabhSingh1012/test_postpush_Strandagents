from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.contracts import MutationMode, PipelineConfig, PostPushContext, WorkItemIdentity  # noqa: E402
from guardrails.post_push.validation import CommandValidationAdapter  # noqa: E402


def _context(profile: str = "full") -> PostPushContext:
    repo_root = Path(__file__).resolve().parents[2]
    return PostPushContext(
        task_id="TASK-001",
        repo=str(repo_root),
        branch="main",
        sha="HEAD",
        identity=WorkItemIdentity(group_id="pr-1", commit_sha="abc", run_id="r1"),
        profile=profile,
    )


class CommandValidationAdapterTests(unittest.TestCase):
    def test_all_checks_pass(self) -> None:
        adapter = CommandValidationAdapter(
            unit_test_cmd="python -c 'print(\"unit ok\")'",
            integration_test_cmd="python -c 'print(\"integration ok\")'",
            build_cmd="python -c 'print(\"build ok\")'",
            typecheck_cmd="python -c 'print(\"type ok\")'",
        )
        result = adapter.validate(_context(profile="full"), PipelineConfig(mutation_mode=MutationMode.OFF))
        self.assertEqual(result.status.value, "pass")
        self.assertEqual(result.blocking_failures, [])
        self.assertEqual([c.name for c in result.checks], ["unit-tests", "integration-tests", "build", "type-check"])

    def test_typecheck_failure_blocks(self) -> None:
        adapter = CommandValidationAdapter(
            unit_test_cmd="python -c 'print(\"unit ok\")'",
            integration_test_cmd="python -c 'print(\"integration ok\")'",
            build_cmd="python -c 'print(\"build ok\")'",
            typecheck_cmd="python -c 'import sys; sys.exit(2)'",
        )
        result = adapter.validate(_context(profile="full"), PipelineConfig(mutation_mode=MutationMode.OFF))
        self.assertEqual(result.status.value, "fail")
        self.assertIn("type-check", result.blocking_failures)

    def test_mutation_threshold_miss_is_advisory_in_light_profile(self) -> None:
        adapter = CommandValidationAdapter(
            unit_test_cmd="python -c 'print(\"unit ok\")'",
            integration_test_cmd="python -c 'print(\"integration ok\")'",
            build_cmd="python -c 'print(\"build ok\")'",
            typecheck_cmd="python -c 'print(\"type ok\")'",
            mutation_cmd="python -c 'print(\"mutation score: 60\")'",
            mutation_threshold=70,
        )
        result = adapter.validate(_context(profile="light"), PipelineConfig(mutation_mode=MutationMode.SAMPLE))
        self.assertEqual(result.status.value, "pass")
        self.assertEqual(result.blocking_failures, [])
        self.assertTrue(any("mutation-threshold-miss" in note for note in result.non_blocking_notes))
        self.assertEqual(result.mutation_score, 60)

    def test_mutation_threshold_miss_blocks_in_full_profile(self) -> None:
        adapter = CommandValidationAdapter(
            unit_test_cmd="python -c 'print(\"unit ok\")'",
            integration_test_cmd="python -c 'print(\"integration ok\")'",
            build_cmd="python -c 'print(\"build ok\")'",
            typecheck_cmd="python -c 'print(\"type ok\")'",
            mutation_cmd="python -c 'print(\"score: 60\")'",
            mutation_threshold=70,
        )
        result = adapter.validate(_context(profile="full"), PipelineConfig(mutation_mode=MutationMode.FULL))
        self.assertEqual(result.status.value, "fail")
        self.assertTrue(any("mutation-threshold-miss" in failure for failure in result.blocking_failures))
        self.assertEqual(result.mutation_score, 60)


if __name__ == "__main__":
    unittest.main()
