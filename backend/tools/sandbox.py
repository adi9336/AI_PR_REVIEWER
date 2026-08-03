"""sandbox — isolates untrusted code execution (M12, Phase 7).

Two layers:

  Policy layer (always on, pure Python):
    scrub_env() drops secret-looking env vars and keeps only a safe
    allowlist. This runs regardless of Docker — defense in depth.

  Docker layer (hard isolation):
    `docker run --rm --network none --memory <mb>m --cpus <n>` with the
    scrubbed env passed inline: no network, no host mounts, resource
    limits, and a hard timeout that force-removes the container (INV-4).

docker_available() gates the live tests; the policy layer is always
tested. If Docker is absent, Sandbox.run() raises SandboxError — fail
closed: an agent must never execute untrusted code unsandboxed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

DOCKER_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.11-slim")

# Env vars a sandboxed process may see. Everything else is dropped.
SAFE_ENV_VARS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "TERM",
        "TMPDIR",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
    }
)

# Substrings that mark an env var as secret and therefore unmountable.
SECRET_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "AUTH",
    "URL",
    "DSN",
)


class SandboxError(Exception):
    """Base class for sandbox failures."""


class SandboxTimeoutError(SandboxError):
    """Raised when a sandboxed command exceeds its timeout (INV-4)."""


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of `env` (default: os.environ) safe for untrusted code.

    Keeps only SAFE_ENV_VARS; drops anything secret-looking by marker.
    """
    source = dict(os.environ) if env is None else dict(env)
    scrubbed: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper in SAFE_ENV_VARS:
            scrubbed[key] = value
        elif any(marker in upper for marker in SECRET_MARKERS):
            continue  # secret-looking — never crosses into the sandbox
    return scrubbed


def docker_available() -> bool:
    """True when the docker CLI works (used to gate live sandbox tests)."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=False
        )
        return probe.returncode == 0
    except Exception:
        return False


@dataclass
class SandboxResult:
    """The outcome of a sandboxed command."""

    exit_code: int
    stdout: str
    stderr: str


class Sandbox:
    """Isolated executor for untrusted code (Docker-backed, fail closed)."""

    def __init__(
        self,
        *,
        image: str = DOCKER_IMAGE,
        memory_mb: int = 512,
        cpus: float = 1.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._image = image
        self._memory_mb = memory_mb
        self._cpus = cpus
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Execute `command` inside an isolated, ephemeral container.

        Raises SandboxError if Docker is unavailable (fail closed) or
        SandboxTimeoutError when the command exceeds the timeout (INV-4).
        """
        if not docker_available():
            raise SandboxError(
                "docker is not available — refusing to run untrusted code unsandboxed"
            )

        # Only CALLER-PROVIDED env crosses into the container — never the
        # host environment. The host's PATH is Windows-flavoured and would
        # break command resolution inside the Linux container; its other
        # variables are a secret leak by default.
        safe_env = scrub_env(dict(env)) if env is not None else {}
        name = f"pr-agent-sbx-{uuid.uuid4().hex[:12]}"
        cmd: list[str] = [
            "docker", "run", "--name", name, "--rm",
            "--network", "none",
            "-m", f"{self._memory_mb}m",
            "--cpus", str(self._cpus),
            "--env", "PYTHONUNBUFFERED=1",
        ]
        for key, value in sorted(safe_env.items()):
            cmd += ["--env", f"{key}={value}"]
        cmd += [self._image, *command]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            # Force-remove the container so a hung payload cannot leak.
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=15
            )
            raise SandboxTimeoutError(
                f"sandboxed command exceeded {self._timeout_seconds}s timeout (INV-4)"
            ) from exc

        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )
