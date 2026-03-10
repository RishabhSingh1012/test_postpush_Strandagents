"""Tests for guardrails.post_push.dedupe: InMemoryDedupeStore."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from guardrails import InMemoryDedupeStore  # noqa: E402


class InMemoryDedupeStoreTests(unittest.TestCase):
    def test_load_open_keys_empty_init_returns_empty_set(self) -> None:
        store = InMemoryDedupeStore()
        self.assertEqual(store.load_open_keys("group-1"), set())

    def test_load_open_keys_after_save_returns_saved_keys(self) -> None:
        store = InMemoryDedupeStore()
        store.save_open_keys("group-1", {"k1", "k2", "k3"})
        self.assertEqual(store.load_open_keys("group-1"), {"k1", "k2", "k3"})

    def test_load_open_keys_different_groups_isolated(self) -> None:
        store = InMemoryDedupeStore()
        store.save_open_keys("g1", {"a", "b"})
        store.save_open_keys("g2", {"c"})
        self.assertEqual(store.load_open_keys("g1"), {"a", "b"})
        self.assertEqual(store.load_open_keys("g2"), {"c"})

    def test_save_open_keys_overwrites_previous_for_same_group(self) -> None:
        store = InMemoryDedupeStore()
        store.save_open_keys("g1", {"old"})
        store.save_open_keys("g1", {"new"})
        self.assertEqual(store.load_open_keys("g1"), {"new"})

    def test_known_issue_ref_without_known_issues_returns_none(self) -> None:
        store = InMemoryDedupeStore()
        self.assertIsNone(store.known_issue_ref("any-key"))

    def test_known_issue_ref_with_known_issues_returns_ref(self) -> None:
        store = InMemoryDedupeStore(known_issues={"key-1": "REF-101", "key-2": "REF-102"})
        self.assertEqual(store.known_issue_ref("key-1"), "REF-101")
        self.assertEqual(store.known_issue_ref("key-2"), "REF-102")

    def test_known_issue_ref_missing_key_returns_none(self) -> None:
        store = InMemoryDedupeStore(known_issues={"key-1": "REF-101"})
        self.assertIsNone(store.known_issue_ref("other-key"))

    def test_known_issues_empty_dict_default(self) -> None:
        store = InMemoryDedupeStore()
        self.assertIsNone(store.known_issue_ref("x"))

    def test_known_issues_none_initialization(self) -> None:
        store = InMemoryDedupeStore(known_issues=None)
        self.assertIsNone(store.known_issue_ref("x"))


if __name__ == "__main__":
    unittest.main()
