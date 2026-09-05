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
from .transport import Raw, Result, Transport

#: Where a copied program lands on the target. Under the user's home rather than
#: /opt or /srv so nothing needs root to work.
REMOTE_ROOT = "~/.runon"

#: The same directory, written so the *remote* shell expands it.
#:
#: REMOTE_ROOT is fine unquoted inside a command — cd, mkdir and scp all let
#: the remote shell expand the tilde. As an exported value it is quoted, and a
#: quoted tilde is a directory that does not exist.
REMOTE_ROOT_EXPR = '"$HOME/.runon"'


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


#: Where a program and the functions library are copied *into*.
#:
#: The parent, not the final path, because `scp -r src dest` means two
#: different things depending on whether dest already exists: it creates dest
#: the first time and puts src *inside* it every time after, so a second copy
#: of hello-world lands in programs/hello-world/hello-world.
REMOTE_PROGRAMS = f"{REMOTE_ROOT}/programs"


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
    # Program parameters are applied after host vars, so a host can be
    # overridden by the program it is running but not the other way round —
    # the program is the more specific statement of intent.
    for key, value in program.params().items():
        env[f"RUNON_PARAM_{key.upper()}"] = value
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
    # scp cannot create the directory it copies into, and on a host that has
    # never been touched nothing has made ~/.runon/programs yet.
    prepared = transport.run(host, f"mkdir -p {REMOTE_PROGRAMS}")
    if not prepared.ok:
        return [
            Result(
                host.name,
                prepared.command,
                prepared.exit_code,
                prepared.stdout,
                f"could not create {REMOTE_PROGRAMS} on the target\n{prepared.stderr}",
            )
        ]

    results = [transport.copy(host, program.path, f"{REMOTE_PROGRAMS}/")]
    if workspace.functions_path.is_dir():
        results.append(transport.copy(host, workspace.functions_path, f"{REMOTE_ROOT}/"))
    return results


def run_program(
    transport: Transport,
    host: Host,
    workspace: Workspace,
    program: Program,
    *,
    args: list[str] | None = None,
    remote: bool = True,
    prompts: dict[str, str] | None = None,
) -> Result:
    if remote:
        directory = remote_program_dir(program.name)
        functions_dir = Raw(f"{REMOTE_ROOT_EXPR}/functions")
    else:
        # Absolute, because the command below cds into the program directory
        # first: a relative path from `runon -C examples` would be resolved
        # against the wrong place by the time the program reads it.
        directory = shlex.quote(str(program.path.resolve()))
        functions_dir = str(workspace.functions_path.resolve())

    argv = " ".join(shlex.quote(a) for a in (args or []))
    command = f"cd {directory} && chmod +x {ENTRY_POINT} 2>/dev/null; ./{ENTRY_POINT}"
    if argv:
        command += f" {argv}"
    # Prompt answers go in last: they were given for this run, which beats
    # anything the host or the program declared earlier.
    return transport.run(host, command, env=program_env(host, program, functions_dir, prompts))


def watch_command(
    workspace: Workspace,
    program: Program,
    *,
    args: list[str] | None = None,
    prompts: dict[str, str] | None = None,
) -> str:
    """The remote command a tmux pane runs.

    Unlike the collected path this keeps the shell open afterwards, so a pane
    shows you what happened instead of closing over it.
    """
    directory = remote_program_dir(program.name)
    argv = " ".join(shlex.quote(a) for a in (args or []))
    exports = " ".join(
        f"{k}={shlex.quote(v)}"
        for k, v in sorted(
            program_env(
                Host("", ""), program, Raw(f"{REMOTE_ROOT_EXPR}/functions"), prompts
            ).items()
        )
        if not k.startswith(("RUNON_HOST", "RUNON_ADDRESS"))
    )
    command = f"cd {directory} && chmod +x {ENTRY_POINT} 2>/dev/null; {exports} ./{ENTRY_POINT}"
    if argv:
        command += f" {argv}"
    return command


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
