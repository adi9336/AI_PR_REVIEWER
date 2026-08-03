#!/usr/bin/env python3
"""ci_check — the local CI gate runner (M13, Phase 18).

Runs the same four gates the CI workflow (.github/workflows/ci.yml) runs:

  1. pytest -q                     — the whole test suite
  2. mypy backend (strict)         — type safety
  3. scripts/check_deps.py         — INV-1 / INV-2 dependency direction
  4. eval gate sanity (M11)        — golden-vs-itself self-check: proves the
                                     dataset + scoring still work with no
                                     secrets. The REAL prompt-regression
                                     canary (agents vs golden set, live LLM)
                                     is `python -m backend.evaluation.canary`,
                                     wired as a secrets-gated CI step.

Exit code is 0 only if ALL gates pass; any failure prints a [FAIL] summary
and exits 1. DB/LLM-gated tests skip when TIGER_DATABASE_URL/OPENAI_API_KEY
are unset (their skipif pattern) — CI runs without secrets.

Usage:
    python scripts/ci_check.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GATES: list[tuple[str, list[str]]] = [
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    ("mypy (strict)", [sys.executable, "-m", "mypy", "backend"]),
    ("check_deps (INV-1/2)", [sys.executable, "scripts/check_deps.py", "--root", "."]),
    (
        "eval gate sanity (M11, self-check)",
        [sys.executable, "-m", "backend.evaluation.regression_gate"],
    ),
]


def run_gate(name: str, cmd: list[str]) -> bool:
    """Run one gate; stream its output; return True iff it exits 0."""
    print(f"== {name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT)
    ok = proc.returncode == 0
    print(f"   -> {'PASS' if ok else 'FAIL'} (exit {proc.returncode})", flush=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    """Run all gates; return 0 iff every gate passes."""
    _ = argv  # reserved for future flags (e.g. --skip-pytest)
    results: dict[str, bool] = {}
    for name, cmd in GATES:
        results[name] = run_gate(name, cmd)

    print("\n===== CI GATE SUMMARY =====")
    for name, ok in results.items():
        print(f"{'[PASS]' if ok else '[FAIL]'} {name}")
    if all(results.values()):
        print("ALL GATES PASS")
        return 0
    print("ONE OR MORE GATES FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
