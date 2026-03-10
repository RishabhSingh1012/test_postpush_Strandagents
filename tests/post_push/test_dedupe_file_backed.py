"""Tests for guardrails.post_push.dedupe: FileBackedDedupeStore."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails.post_push.dedupe import FileBackedDedupeStore  # noqa: E402


class FileBackedDedupeStoreTests(unittest.TestCase):
    def test_save_and_load_open_keys_persist_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".guardrails" / "post-push-dedupe.json"
            store = FileBackedDedupeStore(path=path)
            store.save_open_keys("pr-42", {"k1", "k2"})

            other = FileBackedDedupeStore(path=path)
            self.assertEqual(other.load_open_keys("pr-42"), {"k1", "k2"})

    def test_save_updates_only_target_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".guardrails" / "post-push-dedupe.json"
            store = FileBackedDedupeStore(path=path)
            store.save_open_keys("g1", {"a"})
            store.save_open_keys("g2", {"b"})
            store.save_open_keys("g1", {"a", "c"})

            reload_store = FileBackedDedupeStore(path=path)
            self.assertEqual(reload_store.load_open_keys("g1"), {"a", "c"})
            self.assertEqual(reload_store.load_open_keys("g2"), {"b"})

    def test_known_issue_ref_reads_from_seed_and_persisted_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".guardrails" / "post-push-dedupe.json"
            store = FileBackedDedupeStore(path=path, known_issues={"dedupe:a": "REF-1"})
            store.save_open_keys("g1", {"x"})
            self.assertEqual(store.known_issue_ref("dedupe:a"), "REF-1")

            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["known_issues"]["dedupe:b"] = "REF-2"
            path.write_text(json.dumps(raw), encoding="utf-8")

            reload_store = FileBackedDedupeStore(path=path)
            self.assertEqual(reload_store.known_issue_ref("dedupe:b"), "REF-2")

    def test_corrupt_json_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".guardrails" / "post-push-dedupe.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")

            store = FileBackedDedupeStore(path=path)
            self.assertEqual(store.load_open_keys("g1"), set())
            self.assertIsNone(store.known_issue_ref("x"))


if __name__ == "__main__":
    unittest.main()
