"""Tests for guardrails.post_push.beads_tracker: BeadsTaskTracker."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.beads_tracker import BeadsTaskTracker  # noqa: E402
from guardrails.post_push.contracts import Finding, Severity  # noqa: E402


def _finding(dedupe_key: str = "test:key", title: str = "Test finding") -> Finding:
    return Finding(
        agent="reviewer",
        finding_id="F-001",
        severity=Severity.MEDIUM,
        title=title,
        evidence=["src/a.py:1"],
        recommendation="Fix it",
        dedupe_key=dedupe_key,
    )


class BeadsTaskTrackerLoadRefsTests(unittest.TestCase):
    def test_load_refs_missing_file_returns_empty_dict(self) -> None:
        refs_path = Path("/tmp/nonexistent-beads-refs-xyz/refs.json")
        tracker = BeadsTaskTracker(repo_root=Path("/tmp"), refs_file=refs_path)
        self.assertEqual(tracker.load_refs(), {})

    def test_load_refs_existing_file_returns_mapping(self) -> None:
        tmp = Path("/tmp/beads-test-load")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        refs_path.write_text(json.dumps({"k1": "bd-1", "k2": "bd-2"}) + "\n")
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            self.assertEqual(tracker.load_refs(), {"k1": "bd-1", "k2": "bd-2"})
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()

    def test_get_task_ref_returns_ref_when_present(self) -> None:
        tmp = Path("/tmp/beads-test-get")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        refs_path.write_text(json.dumps({"fingerprint:1": "bd-42"}) + "\n")
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            self.assertEqual(tracker.get_task_ref("fingerprint:1"), "bd-42")
            self.assertIsNone(tracker.get_task_ref("other"))
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()


class BeadsTaskTrackerCreateTaskTests(unittest.TestCase):
    def test_create_task_for_finding_when_ref_exists_returns_existing(self) -> None:
        tmp = Path("/tmp/beads-test-existing")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        refs_path.write_text(json.dumps({"existing:key": "bd-99"}) + "\n")
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            finding = _finding(dedupe_key="existing:key")
            out = tracker.create_task_for_finding(finding)
            self.assertEqual(out, "bd-99")
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()

    def test_create_task_for_finding_calls_bd_and_persists_ref(self) -> None:
        tmp = Path("/tmp/beads-test-create")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            finding = _finding(dedupe_key="new:key", title="New issue")
            with patch("guardrails.post_push.beads_tracker.subprocess.run") as run_bd:
                run_bd.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps({"id": "bd-123"}).encode("utf-8"),
                )
                out = tracker.create_task_for_finding(finding, priority="P2")
            self.assertEqual(out, "bd-123")
            self.assertEqual(tracker.load_refs(), {"new:key": "bd-123"})
            run_bd.assert_called_once()
            args = run_bd.call_args[0][0]
            self.assertEqual(args[0], "bd")
            self.assertEqual(args[1], "create")
            self.assertIn("New issue", args[2])
            self.assertIn("--json", args)
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()

    def test_create_task_for_finding_bd_failure_returns_none(self) -> None:
        tmp = Path("/tmp/beads-test-fail")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            finding = _finding(dedupe_key="fail:key")
            with patch("guardrails.post_push.beads_tracker.subprocess.run") as run_bd:
                run_bd.return_value = MagicMock(returncode=1, stdout=b"")
                out = tracker.create_task_for_finding(finding)
            self.assertIsNone(out)
            self.assertEqual(tracker.load_refs(), {})
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()

    def test_create_task_for_finding_priority_str_maps_to_int(self) -> None:
        tmp = Path("/tmp/beads-test-prio")
        tmp.mkdir(parents=True, exist_ok=True)
        refs_path = tmp / "refs.json"
        try:
            tracker = BeadsTaskTracker(repo_root=tmp, refs_file=refs_path)
            finding = _finding(dedupe_key="prio:key")
            with patch("guardrails.post_push.beads_tracker.subprocess.run") as run_bd:
                run_bd.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps({"id": "bd-1"}).encode("utf-8"),
                )
                tracker.create_task_for_finding(finding, priority="P0")
            args = run_bd.call_args[0][0]  # full command list: ["bd", "create", ...]
            self.assertIn("-p", args)
            idx = args.index("-p")
            self.assertEqual(args[idx + 1], "0")
        finally:
            refs_path.unlink(missing_ok=True)
            tmp.rmdir()


if __name__ == "__main__":
    unittest.main()
