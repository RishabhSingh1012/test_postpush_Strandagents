"""Constants for the post_push package (workspace convention, paths, artifact names, merge decision)."""

from __future__ import annotations

from beartype.typing import Final

# Workspace directory and file names (shared with Pre-Execution)
WORKSPACE_DIR_NAME: Final[str] = ".agentic-workspaces"
RUNTIME_JSON_NAME: Final[str] = "runtime.json"
AGENT_PROMPT_NAME: Final[str] = ".agent-prompt.md"
REPO_STANDARDS_NAME: Final[str] = "repo-standards.md"
PRE_COMMIT_REPORT_NAME: Final[str] = "pre-commit-report.json"
FAILURE_INJECTION_NAME: Final[str] = "failure_injection.json"

# Branch name pattern created by Pre-Execution (full name in runtime.json workspace.branch)
AGENTIC_BRANCH_PREFIX: Final[str] = "agentic/"

# Artifacts root and report filenames
ARTIFACTS_ROOT_DEFAULT: Final[str] = "artifacts"
CLEANUP_REPORT_FILENAME: Final[str] = "cleanup-report.json"
DEEP_VALIDATION_REPORT_FILENAME: Final[str] = "deep-validation-report.json"
AUDIT_FINDINGS_FILENAME: Final[str] = "audit-findings.json"
FOLLOW_UP_TASKS_FILENAME: Final[str] = "follow-up-tasks.json"
MERGE_DECISION_FILENAME: Final[str] = "merge-decision.json"
CURRENT_STATE_SUMMARY_FILENAME: Final[str] = "current-state-summary.md"
POST_PUSH_REPORT_FILENAME: Final[str] = "post-push-report.json"
STAGE_CACHE_FILENAME: Final[str] = "stage-cache.json"
ARTIFACT_KEY_POST_PUSH_REPORT: Final[str] = "post_push_report"
OVERALL_RESULT_PASS: Final[str] = "pass"
OVERALL_RESULT_FAIL: Final[str] = "fail"

# Beads task tracker (refs file under .guardrails)
GUARDRAILS_DIR_NAME: Final[str] = ".guardrails"
BEADS_REFS_FILENAME: Final[str] = "beads-task-refs.json"
POST_PUSH_DEDUPE_FILENAME: Final[str] = "post-push-dedupe.json"
BEADS_CLI_COMMAND: Final[str] = "bd"

# Merge decision triage: decision, reason, status, priorities
MERGE_DECISION_BLOCK: Final[str] = "BLOCK"
MERGE_DECISION_NON_BLOCK: Final[str] = "NON-BLOCK"
DECISION_REASON_HARD_GATE_FAILURE: Final[str] = "hard_gate_failure"
DECISION_REASON_RISK_ESCALATION: Final[str] = "risk_escalation"
DECISION_REASON_ADVISORY_ONLY: Final[str] = "advisory_only"
MERGE_STATUS_BLOCKED: Final[str] = "BLOCKED"
MERGE_STATUS_ALLOW: Final[str] = "ALLOW"
GATE_STATUS_FAIL: Final[str] = "FAIL"
GATE_STATUS_PASS: Final[str] = "PASS"
PRIORITY_P0: Final[str] = "P0"
PRIORITY_P1: Final[str] = "P1"
PRIORITY_P2: Final[str] = "P2"
PRIORITY_P3: Final[str] = "P3"
SOFT_SCORE_THRESHOLD_BLOCK: Final[int] = 70
SOFT_SCORE_THRESHOLD_ELEVATED_P2: Final[int] = 40
FINDING_TYPE_HARD_GATE: Final[str] = "hard-gate"
FINDING_TYPE_SOFT: Final[str] = "soft"
CATEGORY_OTHER: Final[str] = "other"
RATIONALE_HARD_GATE_BLOCKING: Final[str] = "Hard-gate findings are always blocking."
RATIONALE_WEIGHTED_BLOCK: Final[str] = "Weighted risk score {score} meets BLOCK threshold (>=70)."
RATIONALE_ELEVATED_TRACK: Final[str] = "Weighted risk score {score} is elevated (40-69); track via ticket."
RATIONALE_LOW_ADVISORY: Final[str] = "Weighted risk score {score} is low (0-39); advisory follow-up."
RATIONALE_REQUIRED_GATES_POLICY: Final[str] = "Required hard gates are merge-blocking by policy."
DEFAULT_TICKET_VERB: Final[str] = "Investigate"
HARD_GATE_ID_PREFIX: Final[str] = "hard-gate-"
TICKET_OWNER_CI_MAINTAINERS: Final[str] = "ci-maintainers"

# Merge decision: soft categories (frozenset)
SOFT_CATEGORY_SET: Final[frozenset[str]] = frozenset(
    {
        "architecture",
        "optimization",
        "refactor",
        "style",
        "potential-issue",
        "performance",
        "other",
    }
)

# Merge decision: hard gate names (tuple)
HARD_GATES: Final[tuple[str, ...]] = (
    "unit-tests",
    "integration-tests",
    "build",
    "type-check",
    "required-ci",
)
REQUIRED_CI_GATE: Final[str] = "required-ci"

# Merge decision: hard gate name -> aliases for matching validation failures
HARD_GATE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "unit-tests": ("unit-tests", "unit test", "unit"),
    "integration-tests": ("integration-tests", "integration test", "integration"),
    "build": ("build", "compile", "build check"),
    "type-check": ("type-check", "typecheck", "typing", "mypy", "pyright"),
    "required-ci": ("required-ci", "required checks", "required ci", "ci"),
}

# Merge decision: category -> ticket verb
TICKET_VERB_BY_CATEGORY: Final[dict[str, str]] = {
    "architecture": "Refactor",
    "optimization": "Optimize",
    "refactor": "Refactor",
    "style": "Refactor",
    "potential-issue": "Investigate",
    "performance": "Optimize",
    "other": "Investigate",
}

# Merge decision: P0 priority keywords (tuple)
P0_KEYWORDS: Final[tuple[str, ...]] = (
    "security",
    "privacy",
    "data loss",
    "corrupt",
    "production outage",
    "incident",
    "authorization",
    "authentication",
)
