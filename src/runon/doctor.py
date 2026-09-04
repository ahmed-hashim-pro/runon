"""Checking that the machine can do what runon is about to ask of it.

The original tool had an `install_required_packages` command. runon installs
nothing — it has no runtime dependencies and uses tools you already have — so
the useful version of that command is one that tells you what is missing and
what to do about it, rather than reaching for your package manager.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool


def _version(binary: str, *args: str) -> str:
    try:
        out = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""


def run_checks() -> list[Check]:
    checks: list[Check] = []

    ssh = shutil.which("ssh")
    checks.append(
        Check(
            "ssh",
            ssh is not None,
            _version("ssh", "-V") if ssh else "not found — remote commands cannot run",
            required=True,
        )
    )

    scp = shutil.which("scp")
    checks.append(
        Check("scp", scp is not None, scp or "not found — copying cannot work", required=True)
    )

    tmux = shutil.which("tmux")
    checks.append(
        Check(
            "tmux",
            tmux is not None,
            _version("tmux", "-V") if tmux else "not found — --watch and run-layout need it",
            required=False,
        )
    )

    agent = shutil.which("ssh-add")
    loaded = ""
    if agent:
        out = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, check=False)
        if out.returncode == 0:
            loaded = f"{len(out.stdout.strip().splitlines())} key(s) loaded"
        else:
            loaded = "no keys loaded — you will be asked for passwords"
    checks.append(Check("ssh-agent", bool(loaded and "no keys" not in loaded), loaded, False))

    copy_id = shutil.which("ssh-copy-id")
    checks.append(
        Check(
            "ssh-copy-id",
            copy_id is not None,
            copy_id or "not found — install it to stop typing passwords",
            required=False,
        )
    )
    return checks


def report(checks: list[Check], *, stream) -> int:
    """Prints the checks and returns non-zero only if something required is missing."""
    missing_required = 0
    for check in checks:
        if check.ok:
            mark = "ok  "
        elif check.required:
            mark = "MISSING"
            missing_required += 1
        else:
            mark = "--  "
        print(f"  {mark:<8} {check.name:<12} {check.detail}", file=stream)

    if missing_required:
        print("\nrunon cannot reach remote hosts without the missing tools above.", file=stream)
    return 1 if missing_required else 0
