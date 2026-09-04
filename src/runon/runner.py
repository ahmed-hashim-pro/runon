"""Carrying a program to machines and running it there.

The same three verbs work at every scope, because "copy it, run it, or do both"
is the whole vocabulary an operator needs and inventing more would only mean
remembering more.
"""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .inventory import Host
from .program import ENTRY_POINT, Program, Workspace
from .transport import Result, Transport

#: Where a copied program lands on the target. Under the user's home rather than
#: /opt or /srv so nothing needs root to work.
REMOTE_ROOT = "~/.runon"


@dataclass(frozen=True)
class Plan:
    """What is about to happen, so `--dry-run` can print it without doing it."""

    hosts: tuple[Host, ...]
    action: str
    program: str | None = None
    local: Path | None = None
    remote: str | None = None


def remote_program_dir(program_name: str) -> str:
    return f"{REMOTE_ROOT}/programs/{program_name}"


def program_env(
    host: Host,
    program: Program,
    functions_dir: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """What a program can rely on knowing about where it is running.

    Programs get their context from the environment rather than from arguments
    so a script stays runnable by hand: export these and `./main.sh` behaves the
    same way runon would run it.

    RUNON_FUNCTIONS differs by scope — the workspace's own functions directory
    locally, the copied one on a target. A program sources helpers from it
    either way and never has to know which it is.
    """
    env = {
        "RUNON_HOST": host.name,
        "RUNON_ADDRESS": host.address,
        "RUNON_PROGRAM": program.name,
        "RUNON_FUNCTIONS": functions_dir,
    }
    for key, value in host.vars.items():
        env[f"RUNON_VAR_{key.upper()}"] = value
    if extra:
        env.update(extra)
    return env


def copy_program(
    transport: Transport, host: Host, workspace: Workspace, program: Program
) -> list[Result]:
    """Ships the program and the shared functions library alongside it.

    Functions go too because a program that calls one is broken without it, and
    discovering that on the target is a worse place to find out.
    """
    results = [transport.copy(host, program.path, remote_program_dir(program.name))]
    if workspace.functions_path.is_dir():
        results.append(transport.copy(host, workspace.functions_path, f"{REMOTE_ROOT}/functions"))
    return results


def run_program(
    transport: Transport,
    host: Host,
    workspace: Workspace,
    program: Program,
    *,
    args: list[str] | None = None,
    remote: bool = True,
) -> Result:
    if remote:
        directory = remote_program_dir(program.name)
        functions_dir = f"{REMOTE_ROOT}/functions"
    else:
        directory = shlex.quote(str(program.path))
        functions_dir = str(workspace.functions_path)

    argv = " ".join(shlex.quote(a) for a in (args or []))
    command = f"cd {directory} && chmod +x {ENTRY_POINT} 2>/dev/null; ./{ENTRY_POINT}"
    if argv:
        command += f" {argv}"
    return transport.run(host, command, env=program_env(host, program, functions_dir))


def fan_out(
    hosts: list[Host],
    work,
    *,
    parallel: int = 1,
) -> list[Result]:
    """Applies `work` to each host, isolating failures.

    One unreachable machine must not stop the other nineteen: an exception from
    a single host is turned into that host's failing Result and the rest carry
    on. Partial progress is reported rather than hidden behind a traceback.
    """
    if parallel <= 1:
        return [_guard(work, host) for host in hosts]

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(lambda h: _guard(work, h), hosts))


def _guard(work, host: Host) -> Result:
    try:
        outcome = work(host)
    except Exception as exc:  # noqa: BLE001 - one host's failure is data, not a crash
        return Result(host.name, "?", 1, "", str(exc))
    if isinstance(outcome, list):
        # copy_program returns several results; the worst one represents the host
        failed = [r for r in outcome if not r.ok]
        return failed[0] if failed else outcome[0]
    return outcome
