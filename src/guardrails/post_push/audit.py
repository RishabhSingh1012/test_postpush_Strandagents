from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from beartype import beartype

from guardrails.agent_runner import run_agent

from .contracts import DeepValidationResult, Finding, PipelineConfig, PostPushContext, Severity

_DEFAULT_AGENT_TIMEOUT_SEC = 180


@dataclass(frozen=True, slots=True)
class _AgentPrompt:
    role: str
    instructions: str


class _BaseStrandsAuditAgent:
    __slots__ = ("name", "_prompt", "_timeout_sec")

    def __init__(self, name: str, prompt: _AgentPrompt, timeout_sec: int = _DEFAULT_AGENT_TIMEOUT_SEC) -> None:
        self.name = name
        self._prompt = prompt
        self._timeout_sec = timeout_sec

    @beartype
    def run(
        self,
        context: PostPushContext,
        validation: DeepValidationResult,
        config: PipelineConfig,
    ) -> list[Finding]:
        scope_files = self._scope_files(context=context, config=config)
        payload = {
            "repo": context.repo,
            "branch": context.branch,
            "sha": context.sha,
            "profile": context.profile,
            "changed_files": scope_files,
            "validation": validation.to_dict(),
            "role": self._prompt.role,
        }
        output = run_agent(
            agent_name=self.name,
            prompt=self._prompt.instructions,
            payload=payload,
            timeout_sec=self._timeout_sec,
            cwd=context.repo,
        )
        return _parse_findings(agent_name=self.name, raw_output=output)

    def _scope_files(self, context: PostPushContext, config: PipelineConfig) -> list[str]:
        if context.changed_files:
            return context.changed_files
        if context.profile.lower() == "full":
            return _repo_file_sample(context.repo)
        return []


class StrandsAdversarialAgent(_BaseStrandsAuditAgent):
    __slots__ = ()

    def __init__(self, timeout_sec: int = _DEFAULT_AGENT_TIMEOUT_SEC) -> None:
        super().__init__(
            name="adversarial",
            timeout_sec=timeout_sec,
            prompt=_AgentPrompt(
                role="adversarial",
                instructions=(
                    "Analyze changed code for correctness, edge cases, failure modes, and security risks. "
                    "Return strict JSON: {\"findings\":[...]} with finding_id, severity, title, evidence, recommendation, and dedupe_key when possible."
                ),
            ),
        )


class StrandsOptimizationAgent(_BaseStrandsAuditAgent):
    __slots__ = ()

    def __init__(self, timeout_sec: int = _DEFAULT_AGENT_TIMEOUT_SEC) -> None:
        super().__init__(
            name="optimization",
            timeout_sec=timeout_sec,
            prompt=_AgentPrompt(
                role="optimization",
                instructions=(
                    "Analyze changed code for performance, complexity, maintainability, and unnecessary resource usage. "
                    "Return strict JSON: {\"findings\":[...]} with finding_id, severity, title, evidence, recommendation, and dedupe_key when possible."
                ),
            ),
        )


class StrandsReviewerAgent(_BaseStrandsAuditAgent):
    __slots__ = ()

    def __init__(self, timeout_sec: int = _DEFAULT_AGENT_TIMEOUT_SEC) -> None:
        super().__init__(
            name="reviewer",
            timeout_sec=timeout_sec,
            prompt=_AgentPrompt(
                role="reviewer",
                instructions=(
                    "Review changed code for architecture alignment, readability, style, and test adequacy. "
                    "Return strict JSON: {\"findings\":[...]} with finding_id, severity, title, evidence, recommendation, and dedupe_key when possible."
                ),
            ),
        )


def _parse_findings(agent_name: str, raw_output: str) -> list[Finding]:
    if not raw_output.strip():
        return []

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{agent_name} output is not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        raw_findings = payload.get("findings", [])
    elif isinstance(payload, list):
        raw_findings = payload
    else:
        raw_findings = []

    if not isinstance(raw_findings, list):
        raise RuntimeError(f"{agent_name} output schema invalid: findings must be a list.")

    findings: list[Finding] = []
    for index, item in enumerate(raw_findings, start=1):
        if not isinstance(item, dict):
            continue
        findings.append(_to_finding(agent_name=agent_name, index=index, payload=item))
    return findings


def _to_finding(agent_name: str, index: int, payload: dict) -> Finding:
    finding_id = str(payload.get("finding_id") or f"{agent_name[:3].upper()}-{index:03d}")
    title = str(payload.get("title") or "Review finding")
    recommendation = str(payload.get("recommendation") or "Investigate and address the issue.")
    evidence_raw = payload.get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, list) else []
    evidence = [str(item) for item in evidence if str(item).strip()]

    dedupe_key = str(payload.get("dedupe_key") or _fingerprint(agent_name, title, recommendation, evidence))
    severity = _severity_from_text(str(payload.get("severity") or "medium"))
    category = str(payload.get("category") or "other")
    finding_type = str(payload.get("type") or "soft")
    owner = payload.get("owner")
    owner_val = str(owner) if owner is not None else None

    return Finding(
        agent=agent_name,
        finding_id=finding_id,
        severity=severity,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
        dedupe_key=dedupe_key,
        category=category,
        finding_type=finding_type,
        owner=owner_val,
        risk_severity=_optional_int(payload.get("risk_severity")),
        risk_likelihood=_optional_int(payload.get("risk_likelihood")),
        risk_blast_radius=_optional_int(payload.get("risk_blast_radius")),
        risk_detectability=_optional_int(payload.get("risk_detectability")),
    )


def _fingerprint(agent_name: str, title: str, recommendation: str, evidence: list[str]) -> str:
    joined = "|".join([agent_name, title, recommendation, "|".join(evidence[:3])])
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
    return f"{agent_name}:{digest}"


def _severity_from_text(raw: str) -> Severity:
    normalized = raw.strip().lower()
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }
    return mapping.get(normalized, Severity.MEDIUM)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _repo_file_sample(repo_root: str, limit: int = 50) -> list[str]:
    root = Path(repo_root)
    if not root.exists():
        return []
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= limit:
            break
        if not path.is_file():
            continue
        if ".git" in path.parts or "artifacts" in path.parts:
            continue
        files.append(str(path.relative_to(root)))
    return files
