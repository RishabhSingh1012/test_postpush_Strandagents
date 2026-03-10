from __future__ import annotations

import json
from pathlib import Path

from beartype import beartype

from .contracts import PipelineConfig, PostPushContext


class FileArtifactStore:
    __slots__ = ("_repo_root", "_run_dir")

    @beartype
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._run_dir: Path | None = None

    @property
    def run_dir(self) -> Path:
        if self._run_dir is None:
            raise RuntimeError("Artifact store not initialized. Call init_run() first.")
        return self._run_dir

    @beartype
    def init_run(self, context: PostPushContext, config: PipelineConfig) -> Path:
        self._run_dir = (
            self._repo_root
            / config.artifacts_root
            / context.identity.group_id
            / context.identity.commit_sha
            / context.identity.run_id
        )
        self._run_dir.mkdir(parents=True, exist_ok=True)
        return self._run_dir

    @beartype
    def write_json(self, relative_path: str, payload: dict) -> Path:
        target = self.run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return target

    @beartype
    def write_text(self, relative_path: str, content: str) -> Path:
        target = self.run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target
