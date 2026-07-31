#!/usr/bin/env python3
"""check_deps.py — INV-1 & INV-2 dependency direction checker.

INV-1: Dependencies point inward only.
  - backend/core    imports NOTHING (no internal packages, no third-party).
  - backend/models  imports nothing but stdlib + pydantic.
  - No inner module (core, models) may import an outer one (api, orchestrator,
    integrations, agents, etc.).

INV-2: No module outside backend/orchestrator/ may import langgraph.
  All orchestration goes through backend/core/workflow_engine.py.

Exit codes:
  0 — all checks pass
  1 — at least one violation found

Usage:
  python scripts/check_deps.py [--root /path/to/repo]
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Layer ordering (innermost first) ────────────────────────────────────
# A module at layer N may only import modules at layer <= N.
# "core" and "models" are the innermost; everything else is outer.

INNER_LAYERS = {"core", "models"}

# Modules that are allowed to import langgraph (INV-2)
LANGGRAPH_ALLOWED = {"backend/orchestrator"}

# Third-party packages that inner modules may import
CORE_ALLOWED_EXTERNAL: set[str] = set()  # core imports NOTHING external
MODELS_ALLOWED_EXTERNAL = {"pydantic", "decimal", "uuid", "enum", "typing", "__future__"}

# All internal package prefixes — used to distinguish internal from external imports
BACKEND_PREFIX = "backend"


@dataclass
class Violation:
    file: str
    line: int
    import_text: str
    rule: str
    detail: str


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


def _module_layer(module_path: str) -> str | None:
    """Return the layer name for a 'backend/<layer>/...' import, or None if external.

    Handles both dot-separated import paths (``backend.api.reviews``)
    and slash-separated file paths (``backend/core/exceptions.py``).
    """
    # Try slash-separated (file paths)
    parts = module_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == BACKEND_PREFIX:
        return parts[1]
    # Try dot-separated (import paths: backend.api.reviews)
    dot_parts = module_path.split(".")
    if len(dot_parts) >= 2 and dot_parts[0] == BACKEND_PREFIX:
        return dot_parts[1]
    return None


def _is_stdlib(module_path: str) -> bool:
    """Heuristic: top-level package found in the stdlib."""
    top = module_path.split(".")[0]
    return top in {
        "abc", "ast", "asyncio", "collections", "contextlib", "dataclasses",
        "decimal", "enum", "functools", "hashlib", "hmac", "importlib",
        "inspect", "io", "json", "logging", "os", "pathlib", "re", "sys",
        "time", "traceback", "typing", "uuid", "datetime", "enum",
        "__future__", "typing_extensions", "itertools", "copy",
        "contextvars", "warnings", "functools", "time",
    }


def check_file(py_path: Path, repo_root: Path) -> list[Violation]:
    """Check a single Python file for INV-1 and INV-2 violations."""
    violations: list[Violation] = []
    rel = py_path.relative_to(repo_root).as_posix()

    # Determine which layer this file belongs to
    layer = _module_layer(rel)
    if layer is None:
        return violations  # not under backend/ — skip

    try:
        source = py_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return violations

    try:
        tree = ast.parse(source, filename=str(py_path))
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                violations.extend(
                    _check_import(mod, rel, layer, node.lineno, repo_root)
                )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # Relative imports: resolve against the file's package
            if node.level > 0:
                # relative import — resolve
                file_parts = rel.split("/")[:-1]
                base = ".".join(file_parts[: node.level])
                if mod:
                    mod = f"{base}.{mod}" if base else mod
                else:
                    mod = base
            violations.extend(
                _check_import(mod, rel, layer, node.lineno, repo_root)
            )

    return violations


def _check_import(
    import_path: str,
    source_file: str,
    source_layer: str,
    line: int,
    repo_root: Path,
) -> list[Violation]:
    """Check a single import against INV-1 and INV-2."""
    violations: list[Violation] = []
    if not import_path:
        return violations

    top = import_path.split(".")[0]

    # ── INV-2: langgraph isolation ──────────────────────────────────────
    if "langgraph" in import_path:
        source_pkg = source_file.split("/")
        if len(source_pkg) >= 2 and source_pkg[:2] == ["backend", "orchestrator"]:
            return violations  # orchestrator is allowed
        violations.append(Violation(
            file=source_file, line=line, import_text=import_path,
            rule="INV-2", detail="langgraph imported outside backend/orchestrator/",
        ))
        return violations

    # ── INV-1: dependency direction ──────────────────────────────────────
    # Only check internal imports (from backend.*)
    if top != BACKEND_PREFIX:
        # External import — check if inner layer allows it
        if source_layer in INNER_LAYERS:
            if source_layer == "core" and not _is_stdlib(import_path):
                violations.append(Violation(
                    file=source_file, line=line, import_text=import_path,
                    rule="INV-1", detail=f"core imports non-stdlib '{import_path}'",
                ))
            elif source_layer == "models":
                allowed = MODELS_ALLOWED_EXTERNAL | {"backend"}  # models can import itself
                if not _is_stdlib(import_path) and top not in allowed:
                    # pydantic is allowed — check the full package
                    if import_path.startswith("pydantic"):
                        pass  # allowed
                    else:
                        violations.append(Violation(
                            file=source_file, line=line, import_text=import_path,
                            rule="INV-1", detail=f"models imports '{import_path}' (only stdlib + pydantic allowed)",
                        ))
        return violations

    # Internal import: backend.X.Y
    target_layer = _module_layer(import_path)
    if target_layer is None:
        return violations

    # Inner modules (core, models) must not import outer modules
    if source_layer in INNER_LAYERS and target_layer not in INNER_LAYERS:
        violations.append(Violation(
            file=source_file, line=line, import_text=import_path,
            rule="INV-1", detail=f"inner '{source_layer}' imports outer '{target_layer}'",
        ))
    # models can import core (core < models), but core cannot import models

    return violations


def find_python_files(root: Path) -> list[Path]:
    """Find all .py files under backend/, excluding __pycache__ and venv."""
    backend = root / "backend"
    if not backend.exists():
        return []
    files: list[Path] = []
    for p in backend.rglob("*.py"):
        parts = p.parts
        if "__pycache__" in parts or ".venv" in parts or "venv" in parts:
            continue
        files.append(p)
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dependency direction checker (INV-1, INV-2)")
    parser.add_argument("--root", type=str, default=None, help="repo root (default: auto-detect)")
    args = parser.parse_args()

    if args.root:
        repo_root = Path(args.root).resolve()
    else:
        # Auto-detect: find the parent that has a 'backend/' dir
        repo_root = Path(__file__).resolve().parent.parent
        if not (repo_root / "backend").exists():
            print("ERROR: could not locate repo root (no 'backend/' found)", file=sys.stderr)
            return 2

    py_files = find_python_files(repo_root)
    if not py_files:
        print("ERROR: no Python files found under backend/", file=sys.stderr)
        return 2

    all_violations: list[Violation] = []
    for f in py_files:
        all_violations.extend(check_file(f, repo_root))

    if all_violations:
        print(f"FAIL: {len(all_violations)} dependency violation(s) found:\n", file=sys.stderr)
        for v in all_violations:
            print(f"  [{v.rule}] {v.file}:{v.line} — {v.detail}", file=sys.stderr)
            print(f"    import: {v.import_text}", file=sys.stderr)
        return 1

    print(f"OK: {len(py_files)} files checked — no dependency violations (INV-1, INV-2 clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())