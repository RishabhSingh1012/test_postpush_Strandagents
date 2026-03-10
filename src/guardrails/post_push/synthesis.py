from __future__ import annotations

import re
from pathlib import Path

from beartype import beartype

from .contracts import (
    AuditResult,
    CleanupResult,
    DeepValidationResult,
    PipelineConfig,
    PostPushContext,
    StageStatus,
    SynthesisResult,
)


class RepoIntrospectionSynthesis:
    __slots__ = ()

    @beartype
    def synthesize(
        self,
        context: PostPushContext,
        cleanup: CleanupResult,
        validation: DeepValidationResult,
        audit: AuditResult,
        config: PipelineConfig,
    ) -> SynthesisResult:
        _ = config
        repo_root = Path(context.repo)
        if not repo_root.exists():
            return SynthesisResult(
                status=StageStatus.PARTIAL,
                summary_markdown="# Current State Summary\n\nRepository path unavailable.",
                risks=["Repository path unavailable"],
                follow_up_tasks=["Verify repository checkout exists before synthesis."],
                partial_reason="Repository path unavailable",
            )

        modules = _discover_modules(repo_root)
        entry_points = _discover_entry_points(repo_root)
        interfaces = _discover_interfaces(repo_root)
        architecture = _discover_architecture(repo_root)
        workflows = _discover_workflows(repo_root)
        test_posture, coverage = _discover_test_posture(repo_root)
        ui_surfaces, ui_applicable, ui_unavailable = _discover_ui_surfaces(repo_root)
        db_structures, db_applicable, db_unavailable = _discover_database_structures(repo_root)
        ai_eval, ai_applicable, ai_unavailable = _discover_ai_ml_interfaces(repo_root)

        risk_lines = list(validation.blocking_failures)
        risk_lines.extend(item["title"] for item in audit.unresolved_rollups if isinstance(item, dict) and item.get("title"))
        follow_up_tasks = [f"{f.finding_id}: {f.recommendation}" for f in audit.new_findings]

        summary_lines = [
            "# Current State Summary",
            "",
            "## Repository",
            f"- Name: {repo_root.name}",
            f"- Path: {repo_root}",
            f"- Target SHA: {context.sha}",
            f"- Profile: {context.profile}",
            f"- Cleanup status: {cleanup.status.value}",
            f"- Deep validation status: {validation.status.value}",
            f"- Audit status: {audit.status.value}",
            "",
            "## Main Modules/Packages",
            *_bullet_or_placeholder(modules, "No modules discovered."),
            "",
            "## Entry Points",
            *_bullet_or_placeholder(entry_points, "No entry points discovered."),
            "",
            "## Public Interfaces",
            *_bullet_or_placeholder(interfaces, "No public interface signals discovered."),
            "",
            "## Architecture Components",
            *_bullet_or_placeholder(architecture, "No architecture components discovered."),
            "",
            "## Core Workflows",
            *_bullet_or_placeholder(workflows, "No workflow summaries discovered."),
            "",
            "## Test Posture",
            *_bullet_or_placeholder(test_posture, "No tests discovered."),
            f"- Coverage signal: {coverage}",
        ]

        if ui_applicable:
            summary_lines.extend(
                [
                    "",
                    "## UI Surfaces",
                    *_bullet_or_placeholder(ui_surfaces, "UI signals detected, but no routes/components resolved."),
                ]
            )

        if db_applicable:
            summary_lines.extend(
                [
                    "",
                    "## Database Structures",
                    *_bullet_or_placeholder(db_structures, "Database signals detected, but no entities/schemas resolved."),
                ]
            )

        if ai_applicable:
            summary_lines.extend(
                [
                    "",
                    "## AI / ML Evaluation Interfaces",
                    *_bullet_or_placeholder(ai_eval, "AI/ML signals detected, but no eval interfaces resolved."),
                ]
            )

        summary_lines.extend(
            [
                "",
                "## Unresolved Risks",
                *_bullet_or_placeholder(risk_lines, "No unresolved risks detected in this run."),
            ]
        )
        summary = "\n".join(summary_lines)

        partial_reasons: list[str] = []
        if validation.blocking_failures:
            partial_reasons.append("Blocking failures present")
        if ui_unavailable:
            partial_reasons.append("UI section applicable but unavailable")
        if db_unavailable:
            partial_reasons.append("Database section applicable but unavailable")
        if ai_unavailable:
            partial_reasons.append("AI/ML section applicable but unavailable")

        status = StageStatus.PARTIAL if partial_reasons else StageStatus.PASS
        partial_reason = "; ".join(partial_reasons) if partial_reasons else None
        return SynthesisResult(
            status=status,
            summary_markdown=summary,
            risks=risk_lines,
            follow_up_tasks=follow_up_tasks,
            partial_reason=partial_reason,
        )


def _bullet_or_placeholder(items: list[str], placeholder: str) -> list[str]:
    if not items:
        return [f"- {placeholder}"]
    return [f"- {item}" for item in items]


def _discover_modules(repo_root: Path) -> list[str]:
    src_dir = repo_root / "src"
    if not src_dir.is_dir():
        return []
    modules: list[str] = []
    for child in sorted(src_dir.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            modules.append(str(child.relative_to(repo_root)))
    return modules


def _discover_entry_points(repo_root: Path) -> list[str]:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        raw = pyproject.read_text(encoding="utf-8")
    except OSError:
        return []

    matches = re.findall(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"\s*$', raw, flags=re.MULTILINE)
    entry_points: list[str] = []
    in_scripts = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == "[project.scripts]":
            in_scripts = True
            continue
        if in_scripts and stripped.startswith("[") and stripped.endswith("]"):
            in_scripts = False
        if not in_scripts:
            continue
        m = re.match(r'^([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"$', stripped)
        if m:
            entry_points.append(f"{m.group(1)} -> {m.group(2)}")
    if entry_points:
        return entry_points
    # fallback: if section parsing misses, provide unique matches
    return [f"{name} -> {target}" for name, target in matches][:5]


def _discover_interfaces(repo_root: Path) -> list[str]:
    interfaces: list[str] = []
    cli_py = repo_root / "src" / "guardrails" / "cli.py"
    if cli_py.is_file():
        try:
            raw = cli_py.read_text(encoding="utf-8")
            commands = re.findall(r'@cli\.command\("([^"]+)"\)', raw)
            interfaces.extend([f"CLI command: {cmd}" for cmd in commands])
        except OSError:
            pass

    for py in sorted((repo_root / "src").rglob("*.py")):
        if len(interfaces) >= 20:
            break
        try:
            raw = py.read_text(encoding="utf-8")
        except OSError:
            continue
        routes = re.findall(r"@app\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']", raw)
        for method, route in routes:
            interfaces.append(f"API route: {method.upper()} {route} ({py.relative_to(repo_root)})")
            if len(interfaces) >= 20:
                break

    seen: set[str] = set()
    unique: list[str] = []
    for item in interfaces:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _discover_architecture(repo_root: Path) -> list[str]:
    src_dir = repo_root / "src"
    if not src_dir.is_dir():
        return []
    components: list[str] = []
    for pkg in sorted(src_dir.rglob("*")):
        if not pkg.is_dir():
            continue
        if pkg.name.startswith(".") or pkg.name == "__pycache__":
            continue
        # include only shallow-ish logical components
        rel = pkg.relative_to(repo_root)
        if len(rel.parts) <= 4:
            components.append(str(rel))
    return components[:15]


def _discover_workflows(repo_root: Path) -> list[str]:
    readme = repo_root / "README.md"
    if not readme.is_file():
        return []
    try:
        raw = readme.read_text(encoding="utf-8")
    except OSError:
        return []
    headings = re.findall(r"^###\s+(.+)$", raw, flags=re.MULTILINE)
    return [f"{heading.strip()} workflow present." for heading in headings[:8]]


def _discover_test_posture(repo_root: Path) -> tuple[list[str], str]:
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return [], "not available"
    posture: list[str] = []
    test_files = list(tests_dir.rglob("test_*.py"))
    posture.append(f"Total test files: {len(test_files)}")
    names = {path.parent.name.lower() for path in test_files}
    if "integration" in names or "integration" in {part.lower() for path in test_files for part in path.parts}:
        posture.append("Integration tests: present")
    if "unit" in names or "unit" in {part.lower() for path in test_files for part in path.parts}:
        posture.append("Unit tests: present")
    if not any("integration" in item.lower() or "unit" in item.lower() for item in posture):
        posture.append("Unit/Integration split: not explicitly labeled")

    coverage = "not available"
    coverage_xml = repo_root / "coverage.xml"
    if coverage_xml.is_file():
        try:
            raw = coverage_xml.read_text(encoding="utf-8")
            match = re.search(r'line-rate="([0-9.]+)"', raw)
            if match:
                coverage = f"{round(float(match.group(1)) * 100, 2)}%"
        except (OSError, ValueError):
            coverage = "not available"
    return posture, coverage


def _discover_ui_surfaces(repo_root: Path) -> tuple[list[str], bool, bool]:
    indicators = (
        repo_root / "frontend",
        repo_root / "ui",
        repo_root / "web",
        repo_root / "src" / "ui",
        repo_root / "src" / "frontend",
    )
    file_patterns = ("*.tsx", "*.jsx", "*.vue", "*.svelte")
    applicable = any(path.exists() for path in indicators)
    files: list[Path] = []
    for pattern in file_patterns:
        matched = list(repo_root.rglob(pattern))
        if matched:
            applicable = True
            files.extend(matched)
    if not applicable:
        return [], False, False

    items: list[str] = []
    try:
        for path in sorted(files)[:20]:
            rel = path.relative_to(repo_root)
            text = path.read_text(encoding="utf-8")
            route_hits = re.findall(r"path\s*[:=]\s*[\"']([^\"']+)[\"']", text)
            if route_hits:
                for route in route_hits[:2]:
                    items.append(f"Route: {route} ({rel})")
            else:
                items.append(f"Component/module: {rel}")
    except OSError:
        return [], True, True
    return _unique(items), True, len(items) == 0


def _discover_database_structures(repo_root: Path) -> tuple[list[str], bool, bool]:
    indicators = (
        repo_root / "migrations",
        repo_root / "alembic",
        repo_root / "alembic.ini",
        repo_root / "db",
        repo_root / "database",
    )
    applicable = any(path.exists() for path in indicators)
    model_files = [path for path in repo_root.rglob("*.py") if path.name in {"models.py", "schema.py", "schemas.py"}]
    if model_files:
        applicable = True
    if not applicable:
        return [], False, False

    items: list[str] = []
    try:
        for directory in ("migrations", "alembic", "db", "database"):
            path = repo_root / directory
            if path.exists():
                items.append(f"Structure root: {path.relative_to(repo_root)}")
        for path in sorted(model_files)[:10]:
            rel = path.relative_to(repo_root)
            raw = path.read_text(encoding="utf-8")
            classes = re.findall(r"^class\s+([A-Za-z0-9_]+)\(", raw, flags=re.MULTILINE)
            if classes:
                for cls in classes[:2]:
                    items.append(f"Entity: {cls} ({rel})")
            else:
                items.append(f"Schema/module: {rel}")
    except OSError:
        return [], True, True
    return _unique(items), True, len(items) == 0


def _discover_ai_ml_interfaces(repo_root: Path) -> tuple[list[str], bool, bool]:
    indicators = (
        repo_root / "ml",
        repo_root / "models",
        repo_root / "eval",
        repo_root / "evaluation",
        repo_root / "notebooks",
    )
    applicable = any(path.exists() for path in indicators)
    eval_files = [path for path in repo_root.rglob("*.py") if "eval" in path.stem.lower() or "benchmark" in path.stem.lower()]
    if eval_files:
        applicable = True
    if not applicable:
        return [], False, False

    items: list[str] = []
    try:
        for path in sorted(eval_files)[:12]:
            rel = path.relative_to(repo_root)
            raw = path.read_text(encoding="utf-8")
            metrics = re.findall(r"(accuracy|f1|precision|recall|bleu|rouge|mse|mae)", raw, flags=re.IGNORECASE)
            if metrics:
                unique_metrics = ",".join(sorted({m.lower() for m in metrics})[:3])
                items.append(f"Eval pipeline: {rel} (metrics: {unique_metrics})")
            else:
                items.append(f"Eval pipeline: {rel}")
        for dataset_file in sorted(repo_root.rglob("*")):
            if len(items) >= 20:
                break
            if not dataset_file.is_file():
                continue
            if dataset_file.suffix.lower() not in {".csv", ".jsonl", ".parquet"}:
                continue
            name = dataset_file.name.lower()
            if "benchmark" in name or "dataset" in name or "eval" in name:
                items.append(f"Benchmark dataset: {dataset_file.relative_to(repo_root)}")
    except OSError:
        return [], True, True
    return _unique(items), True, len(items) == 0


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items
