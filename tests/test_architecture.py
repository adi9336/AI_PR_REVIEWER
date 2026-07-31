"""M1 architecture tests — dependency direction (INV-1) and engine isolation (INV-2).

Two directions are tested:
  1. The clean tree passes check_deps.py (exit 0).
  2. A deliberate inward→outward violation is detected (exit non-zero).
  3. A deliberate langgraph import outside orchestrator is detected (exit non-zero).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_DEPS = REPO_ROOT / "scripts" / "check_deps.py"


def _run_checker(cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run check_deps.py and capture the result."""
    return subprocess.run(
        [sys.executable, str(CHECK_DEPS), "--root", str(cwd)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── 1. clean tree passes ────────────────────────────────────────────────
def test_clean_tree_passes():
    """The repo as committed must pass check_deps.py with exit 0."""
    proc = _run_checker()
    assert proc.returncode == 0, (
        f"check_deps.py failed on clean tree:\n{proc.stderr}\n{proc.stdout}"
    )
    assert "OK" in proc.stdout


# ── 2. deliberate INV-1 violation is caught ─────────────────────────────
def test_inv1_inner_imports_outer_is_caught(tmp_path: Path):
    """If core/exceptions.py imports an outer module, check_deps must exit non-zero."""
    # Create a mini repo structure
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "backend" / "__init__.py").write_text("")

    (tmp_path / "backend" / "core").mkdir(exist_ok=True)
    (tmp_path / "backend" / "core" / "__init__.py").write_text("")
    (tmp_path / "backend" / "core" / "exceptions.py").write_text(
        '"""Violates INV-1: core imports an outer module."""\n'
        "from __future__ import annotations\n"
        "from backend.api.reviews import router  # INV-1 violation\n"
    )

    (tmp_path / "backend" / "api").mkdir(exist_ok=True)
    (tmp_path / "backend" / "api" / "__init__.py").write_text("")
    (tmp_path / "backend" / "api" / "reviews.py").write_text(
        '"""stub"""\nrouter = None\n'
    )

    proc = _run_checker(cwd=tmp_path)
    assert proc.returncode == 1, (
        f"check_deps.py should have caught INV-1 violation but passed:\n{proc.stdout}"
    )
    assert "INV-1" in proc.stderr


# ── 3. deliberate INV-2 violation is caught ─────────────────────────────
def test_inv2_langgraph_outside_orchestrator_is_caught(tmp_path: Path):
    """If a non-orchestrator module imports langgraph, check_deps must exit non-zero."""
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "backend" / "__init__.py").write_text("")

    (tmp_path / "backend" / "agents").mkdir(exist_ok=True)
    (tmp_path / "backend" / "agents" / "__init__.py").write_text("")
    (tmp_path / "backend" / "agents" / "base_agent.py").write_text(
        '"""Violates INV-2: agents imports langgraph."""\n'
        "from __future__ import annotations\n"
        "import langgraph.graph  # INV-2 violation\n"
    )

    proc = _run_checker(cwd=tmp_path)
    assert proc.returncode == 1, (
        f"check_deps.py should have caught INV-2 violation but passed:\n{proc.stdout}"
    )
    assert "INV-2" in proc.stderr


# ── 4. orchestrator IS allowed to import langgraph ──────────────────────
def test_inv2_orchestrator_langgraph_is_allowed(tmp_path: Path):
    """backend/orchestrator/ IS allowed to import langgraph — must not flag."""
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "backend" / "__init__.py").write_text("")

    (tmp_path / "backend" / "orchestrator").mkdir(exist_ok=True)
    (tmp_path / "backend" / "orchestrator" / "__init__.py").write_text("")
    (tmp_path / "backend" / "orchestrator" / "graph.py").write_text(
        '"""Legal: orchestrator imports langgraph."""\n'
        "from __future__ import annotations\n"
        "import langgraph.graph  # legal — INV-2 allows this\n"
    )

    proc = _run_checker(cwd=tmp_path)
    assert proc.returncode == 0, (
        f"check_deps.py should allow orchestrator langgraph import:\n{proc.stderr}"
    )


# ── 5. models importing pydantic is allowed ─────────────────────────────
def test_inv1_models_pydantic_is_allowed(tmp_path: Path):
    """backend/models/ importing pydantic is legal — must not flag as INV-1."""
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "backend" / "__init__.py").write_text("")

    (tmp_path / "backend" / "models").mkdir(exist_ok=True)
    (tmp_path / "backend" / "models" / "__init__.py").write_text("")
    (tmp_path / "backend" / "models" / "findings.py").write_text(
        '"""Legal: models imports pydantic."""\n'
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\n"
        "class Finding(BaseModel):\n    pass\n"
    )

    proc = _run_checker(cwd=tmp_path)
    assert proc.returncode == 0, (
        f"check_deps.py should allow models pydantic import:\n{proc.stderr}"
    )