from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.audit import (  # noqa: E402
    StrandsAdversarialAgent,
    StrandsOptimizationAgent,
    StrandsReviewerAgent,
)
from guardrails.post_push.contracts import (  # noqa: E402
    DeepValidationResult,
    PipelineConfig,
    PostPushContext,
    StageStatus,
    WorkItemIdentity,
)


def _context(changed_files: list[str] | None = None) -> PostPushContext:
    repo_root = Path(__file__).resolve().parents[2]
    return PostPushContext(
        task_id="TASK-001",
        repo=str(repo_root),
        branch="main",
        sha="abc123",
        identity=WorkItemIdentity(group_id="pr-1", commit_sha="abc123", run_id="r1"),
        profile="full",
        changed_files=changed_files or ["src/guardrails/post_push/pipeline.py"],
    )


class StrandsAuditAgentsTests(unittest.TestCase):
    def test_reviewer_parses_json_findings(self) -> None:
        agent = StrandsReviewerAgent()
        response = {
            "findings": [
                {
                    "finding_id": "REV-101",
                    "severity": "high",
                    "title": "Missing retry bound",
                    "evidence": ["src/service.py:88"],
                    "recommendation": "Set explicit retry max.",
                    "dedupe_key": "reviewer:missing-retry-bound",
                    "category": "potential-issue",
                }
            ]
        }
        with patch("guardrails.post_push.audit.run_agent", return_value=json.dumps(response)) as run_agent_mock:
            findings = agent.run(
                context=_context(),
                validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                config=PipelineConfig(),
            )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "REV-101")
        self.assertEqual(findings[0].agent, "reviewer")
        self.assertEqual(findings[0].dedupe_key, "reviewer:missing-retry-bound")
        args = run_agent_mock.call_args.kwargs
        self.assertIn("changed_files", args["payload"])
        self.assertEqual(args["payload"]["changed_files"], ["src/guardrails/post_push/pipeline.py"])

    def test_optimization_generates_fingerprint_when_missing(self) -> None:
        agent = StrandsOptimizationAgent()
        response = {
            "findings": [
                {
                    "severity": "medium",
                    "title": "Inefficient list scan",
                    "evidence": ["src/a.py:10"],
                    "recommendation": "Use dict lookup.",
                }
            ]
        }
        with patch("guardrails.post_push.audit.run_agent", return_value=json.dumps(response)):
            findings = agent.run(
                context=_context(),
                validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                config=PipelineConfig(),
            )
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].dedupe_key.startswith("optimization:"))
        self.assertEqual(findings[0].agent, "optimization")

    def test_adversarial_raises_on_invalid_json(self) -> None:
        agent = StrandsAdversarialAgent()
        with patch("guardrails.post_push.audit.run_agent", return_value="not-json"):
            with self.assertRaises(RuntimeError):
                agent.run(
                    context=_context(),
                    validation=DeepValidationResult(status=StageStatus.PASS, checks=[]),
                    config=PipelineConfig(),
                )


if __name__ == "__main__":
    unittest.main()
