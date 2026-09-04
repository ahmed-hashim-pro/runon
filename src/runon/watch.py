"""Watching a program run, one pane per host.

The original tool drove Yakuake so an operator could see each machine working
in its own pane. Yakuake is KDE-only, so this uses tmux — but the capability is
the point and dropping it was a real loss: for a long program, "2/3 ok" arriving
five minutes later tells you far less than watching the one that is stuck.

This deliberately does not capture output. The panes are attached to a terminal
and the operator reads them; anything that captured them would just be the
normal path with extra steps.
"""

from __future__ import annotations

import shutil
import subprocess

from .errors import RunonError
from .inventory import Host

SESSION_PREFIX = "runon"


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def session_name(label: str) -> str:
    """tmux treats dots and colons as address separators, so they cannot appear."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
    return f"{SESSION_PREFIX}-{cleaned}"[:60]


def build_commands(hosts: list[Host], ssh_argv: list[str], remote_command: str) -> list[list[str]]:
    """One full ssh argv per host, for tmux to run in its own pane."""
    return [[*ssh_argv, host.ssh_target, remote_command] for host in hosts]


def open_panes(
    hosts: list[Host],
    commands: list[list[str]],
    *,
    label: str,
    attach: bool = True,
    runner=subprocess.run,
) -> str:
    """Opens a tmux session with one pane per host and returns its name.

    Panes are kept open after the command exits — `remain-on-exit` — because a
    pane that vanishes takes the error message with it, which is precisely the
    moment you were watching for.
    """
    if not tmux_available():
        raise RunonError(
            "tmux is not installed, and --watch needs it.\n"
            "Install tmux, or drop --watch to have results reported when each host finishes."
        )
    if not hosts:
        raise RunonError("no hosts to watch")

    session = session_name(label)
    first, *rest = zip(hosts, commands, strict=True)

    runner(["tmux", "new-session", "-d", "-s", session, *first[1]], check=False)
    runner(["tmux", "set-option", "-t", session, "remain-on-exit", "on"], check=False)
    runner(["tmux", "select-pane", "-t", f"{session}.0", "-T", first[0].name], check=False)

    for index, (host, command) in enumerate(rest, start=1):
        runner(["tmux", "split-window", "-t", session, *command], check=False)
        runner(["tmux", "select-pane", "-t", f"{session}.{index}", "-T", host.name], check=False)
        # Re-tiling after each split keeps twenty panes from becoming twenty
        # slivers one line tall.
        runner(["tmux", "select-layout", "-t", session, "tiled"], check=False)

    runner(["tmux", "set-option", "-t", session, "pane-border-status", "top"], check=False)

    if attach:
        runner(["tmux", "attach-session", "-t", session], check=False)
    return session
