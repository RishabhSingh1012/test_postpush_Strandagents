# Post-Push Guardrails

This package implements the post-push gate for the repository. It runs after code is pushed and produces traceable artifacts for cleanup, deep validation, audit analysis, and current-state synthesis.

## Goals

1. Safely clean up harness-managed local artifacts.
2. Run deeper validation checks than earlier gates.
3. Run staged audit agents and deduplicate findings across pushes.
4. Generate a current-state repository synthesis at the target SHA.
5. Emit a single index report with all artifact links.

## Entry Points

1. Wrapper script: `tools/post-push-gate`
2. CLI command: `guardrails post-push`

Wrapper examples:

```bash
./tools/post-push-gate light
./tools/post-push-gate full --pr 482 --sha a1b2c3d
POST_PUSH_PROFILE=full ./tools/post-push-gate --group-id pr-482
```

CLI examples:

```bash
guardrails post-push --profile light
guardrails post-push --profile full --pr 482 --sha a1b2c3d --runtime-id 12345
```

## CLI Options

`guardrails post-push` supports:

1. `--task-id` (optional; derived if omitted)
2. `--profile <light|full>`
3. `--group-id <id>`
4. `--sha <commit-ish>`
5. `--run-id <id>`
6. `--branch <name>`
7. `--pr <id/url>`
8. `--runtime-id <id>`
9. `--cleanup <safe|off>`
10. `--mutation <off|sample|full>`
11. `--artifacts-dir <path>`
12. `--beads`

Task id fallback order when `--task-id` is omitted:

1. `--group-id`
2. PR-derived `pr-<n>`
3. Branch-derived `branch-<name>`
4. SHA-derived `sha-<shortsha>`

## Identity and Artifact Layout

Work item identity:

1. `group_id` groups related pushes (PR or branch)
2. `commit_sha` is immutable target state
3. `run_id` identifies an execution attempt

Artifact root:

```text
artifacts/<group_id>/<commit_sha>/<run_id>/
```

Primary artifacts:

1. `cleanup-report.json`
2. `deep-validation-report.json`
3. `audit-findings.json`
4. `follow-up-tasks.json`
5. `merge-decision.json`
6. `current-state-summary.md`
7. `post-push-report.json`
8. `stage-cache.json`

## Pipeline Overview

`PostPushPipeline` runs these stages in order:

1. Cleanup
2. Deep validation
3. Audits
4. Synthesis
5. Merge decision triage

Notes:

1. Cleanup runs first and is safety-first.
2. Audits can still run even when validation fails if configured.
3. Synthesis executes even on partial/failing runs when possible.
4. Report and artifacts are always written.

## Stage 1: Cleanup (`cleanup.py`)

Adapter: `ManagedSafeCleanup`

What it can clean:

1. Harness workspace under `.agentic-workspaces/<task_id>`
2. Harness branch prefixed with `agentic/`

Safety behavior:

1. Rejects workspace deletion outside managed root.
2. Never touches remote branches.
3. Checks unmerged local commits before cleanup.
4. If unmerged commits exist, both branch and workspace cleanup are skipped for that task.

Common skip reasons:

1. `cleanup_mode_off`
2. `runtime_metadata_missing`
3. `not_harness_managed`
4. `workspace_missing`
5. `branch_not_found`
6. `unmerged_local_commits`

Status rules:

1. `pass` when cleanup actions succeed with no skips
2. `partial` when non-destructive skips occur
3. `fail` when policy violations occur

## Stage 2: Deep Validation (`validation.py`)

Adapter: `CommandValidationAdapter`

Checks:

1. Unit tests
2. Integration tests
3. Build check
4. Type check
5. Mutation check (optional by mode)

Default commands:

1. Unit: `pytest -q`
2. Integration: `pytest -q`
3. Build: `python -m compileall -q src`
4. Type check: `python -m mypy src`

Environment overrides:

1. `POST_PUSH_UNIT_TEST_CMD`
2. `POST_PUSH_INTEGRATION_TEST_CMD`
3. `POST_PUSH_BUILD_CMD`
4. `POST_PUSH_TYPECHECK_CMD`
5. `POST_PUSH_MUTATION_CMD`
6. `POST_PUSH_MUTATION_TARGETS`

Mutation modes:

1. `off`
2. `sample`
3. `full`

Profile behavior:

1. `light` default mutation is `off`
2. `full` default mutation is `sample`
3. Mutation threshold miss is advisory in `light`
4. Mutation threshold miss is blocking in `full`

## Stage 3: Audit Pipeline (`audit.py` + `agent_runner.py`)

Agents run sequentially:

1. `adversarial`
2. `optimization`
3. `reviewer`

Runner:

1. Uses Strands command template from `STRANDS_RUN_COMMAND_TEMPLATE`
2. Default template: `strands run --agent {agent} --json`

Scope behavior:

1. Uses diff-aware `changed_files` when available
2. In `full` profile with no changed files, agent may use sampled repo context

Finding contract includes:

1. `agent`
2. `finding_id`
3. `severity`
4. `title`
5. `evidence`
6. `recommendation`
7. `dedupe_key`

Dedupe behavior:

1. Prior open keys loaded per `group_id`
2. New findings returned in full
3. Existing still-open findings returned as rollups
4. Resolved keys removed from open set and emitted as `resolved_rollups`
5. Known-issue references are emitted and suppressed from new findings

Audit failure policy:

1. Errors are always recorded in `audit-findings.json`
2. In `full` profile, overall result fails when all configured audit agents fail to execute
3. In `light` profile, audit execution errors are advisory

## Stage 4: Current-State Synthesis (`synthesis.py`)

Adapter: `RepoIntrospectionSynthesis`

Always targets repository truth at the selected SHA.

Base sections:

1. Repository identity
2. Main modules/packages
3. Entry points
4. Public interfaces
5. Architecture components
6. Core workflows
7. Test posture
8. Unresolved risks

Conditional sections (when signals exist):

1. UI Surfaces
2. Database Structures
3. AI / ML Evaluation Interfaces

Partial signaling:

1. `partial` if blocking validation failures exist
2. `partial` if a conditional section is applicable but unavailable
3. `partial_reason` explains why

## Merge Decision Triage (`merge_decision.py`)

`RuleBasedMergeDecisionTriage` classifies outcomes into blocking and non-blocking with priorities.

Hard gates considered:

1. unit-tests
2. integration-tests
3. build
4. type-check
5. required-ci

Output:

1. Decision: `BLOCK` or `NON-BLOCK`
2. Priority: `P0`..`P3`
3. Merge status: `BLOCKED` or `ALLOW`
4. Per-finding ticket metadata and rationale

## Stage Cache and Rerun Strategy

Cache file:

1. `stage-cache.json` stored in each run directory

Behavior:

1. Loads cache from latest prior run for same `group_id` and `commit_sha`
2. Reuses only stages with matching fingerprint and `pass` status
3. Re-runs failed/partial/missing stages
4. SHA change naturally causes cache miss because path and fingerprints differ
5. Missing/corrupt cache never blocks execution

## Dedupe Store (`dedupe.py`)

Default file:

1. `.guardrails/post-push-dedupe.json`

Structure:

1. `open_keys_by_group`
2. `known_issues`
3. `updated_at`

Store implementations:

1. `FileBackedDedupeStore`
2. `InMemoryDedupeStore` (tests/defaults)

## Beads Task Tracking (`beads_tracker.py`)

Optional integration via `--beads`:

1. Loads known task references for dedupe suppression
2. Creates task for new findings when needed
3. Persists dedupe key to task id mapping in `.guardrails/beads-task-refs.json`

Requires `bd` CLI in `PATH`.

## Report Shape (`post-push-report.json`)

Top-level keys include:

1. `work_item_id`
2. `repo`, `branch`, `sha`, `pr`, `profile`, `runtime_id`
3. `overall_result`
4. `cleanup`
5. `deep_validation`
6. `agents`
7. `synthesis`
8. `merge_decision`
9. `next_actions`
10. `artifacts`
11. `generated_at`
12. `rerun`

Agent summary includes:

1. Total agent count
2. New findings count
3. Rollup count
4. Error count
5. Failed agent names
6. Per-agent findings for `adversarial`, `optimization`, `reviewer`

Synthesis report includes:

1. `status`, `risks`, `follow_up_tasks`, `partial_reason`
2. `path` to `current-state-summary.md`

## Module Map

1. `pipeline.py`: orchestration, caching, report assembly
2. `cleanup.py`: managed cleanup with safety guardrails
3. `validation.py`: command-based deep checks and mutation policy
4. `audit.py`: staged Strands audit agents and parsing
5. `synthesis.py`: repo introspection summary generation
6. `merge_decision.py`: blocking vs advisory triage
7. `contracts.py`: immutable stage/result models
8. `interfaces.py`: adapter protocols
9. `artifacts.py`: file artifact writer
10. `dedupe.py`: open-finding and known-issue persistence
11. `beads_tracker.py`: optional task linkage
12. `workspace.py`: shared workspace path utilities
13. `defaults.py`: lightweight defaults and test helpers
14. `constants.py`: filenames, decision constants, policy constants

## Testing

Run post-push focused tests:

```bash
pytest -q tests/post_push tests/test_cli_post_push.py
```

Run all tests:

```bash
pytest -q
```

## Troubleshooting

1. `Strands command unavailable`: install/configure Strands or set `STRANDS_RUN_COMMAND_TEMPLATE`.
2. Cleanup skipped unexpectedly: inspect `cleanup-report.json` skip reasons.
3. Validation blocking failure: inspect `deep-validation-report.json` check details.
4. Repeated finding noise: inspect `.guardrails/post-push-dedupe.json` and `audit-findings.json`.
5. Missing Beads integration: ensure `bd` exists and project is initialized with `bd init`.
