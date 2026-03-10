from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from beartype import beartype


class InMemoryDedupeStore:
    __slots__ = ("_open", "_known_issues")

    @beartype
    def __init__(self, known_issues: dict[str, str] | None = None) -> None:
        self._open: dict[str, set[str]] = {}
        self._known_issues = known_issues or {}

    @beartype
    def load_open_keys(self, group_id: str) -> set[str]:
        return set(self._open.get(group_id, set()))

    @beartype
    def save_open_keys(self, group_id: str, dedupe_keys: set[str]) -> None:
        self._open[group_id] = set(dedupe_keys)

    @beartype
    def known_issue_ref(self, dedupe_key: str) -> str | None:
        return self._known_issues.get(dedupe_key)


class FileBackedDedupeStore:
    __slots__ = ("_path", "_known_issues")

    @beartype
    def __init__(self, path: Path, known_issues: dict[str, str] | None = None) -> None:
        self._path = path
        self._known_issues = known_issues or {}

    @beartype
    def load_open_keys(self, group_id: str) -> set[str]:
        state = self._read_state()
        groups = state.get("open_keys_by_group", {})
        if not isinstance(groups, dict):
            return set()
        raw = groups.get(group_id, [])
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw if isinstance(item, str)}

    @beartype
    def save_open_keys(self, group_id: str, dedupe_keys: set[str]) -> None:
        state = self._read_state()
        groups = state.get("open_keys_by_group", {})
        if not isinstance(groups, dict):
            groups = {}
        groups[group_id] = sorted(set(dedupe_keys))
        state["open_keys_by_group"] = groups

        known = state.get("known_issues", {})
        if not isinstance(known, dict):
            known = {}
        known.update(self._known_issues)
        state["known_issues"] = {str(k): str(v) for k, v in known.items() if isinstance(k, str) and isinstance(v, str)}
        state["updated_at"] = _utc_now()
        self._write_state(state)

    @beartype
    def known_issue_ref(self, dedupe_key: str) -> str | None:
        if dedupe_key in self._known_issues:
            return self._known_issues[dedupe_key]

        state = self._read_state()
        known = state.get("known_issues", {})
        if not isinstance(known, dict):
            return None
        value = known.get(dedupe_key)
        return str(value) if isinstance(value, str) else None

    def _read_state(self) -> dict:
        if not self._path.is_file():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _write_state(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temp.replace(self._path)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
