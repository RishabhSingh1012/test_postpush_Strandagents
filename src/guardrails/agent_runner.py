"""Shared agent invocation abstraction for guardrails.

Post-push audit stages call this function to run external agent frameworks.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from beartype import beartype
from beartype.typing import Any

_DEFAULT_STRANDS_COMMAND = "strands run --agent {agent} --json"


@beartype
def run_agent(
    agent_name: str,
    prompt: str,
    payload: dict[str, Any],
    timeout_sec: int = 120,
    cwd: Path | str | None = None,
) -> str:
    """Run an external agent and return stdout.

    This implementation is intentionally Strands-first for post-push audits.
    Override command format with STRANDS_RUN_COMMAND_TEMPLATE when needed.

    Template variables supported in STRANDS_RUN_COMMAND_TEMPLATE:
    - {agent}
    """
    command_template = os.getenv("STRANDS_RUN_COMMAND_TEMPLATE", _DEFAULT_STRANDS_COMMAND)
    rendered = command_template.format(agent=agent_name)
    command = shlex.split(rendered)
    binary = command[0] if command else ""

    if not binary or shutil.which(binary) is None:
        raise RuntimeError(
            f"Strands command unavailable. Configure STRANDS_RUN_COMMAND_TEMPLATE or install '{binary or 'strands'}'."
        )

    request_payload = {
        "agent": agent_name,
        "prompt": prompt,
        "payload": payload,
    }
    stdin_data = json.dumps(request_payload)
    result = subprocess.run(
        command,
        input=stdin_data,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "agent command failed"
        raise RuntimeError(f"Agent '{agent_name}' failed ({result.returncode}): {message}")
    return result.stdout
