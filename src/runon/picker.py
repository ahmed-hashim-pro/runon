"""Choosing a program when you did not name one."""

from __future__ import annotations

import sys

from .program import Program


def choose(programs: list[Program], *, stream=None, prompt_stream=None) -> Program | None:
    """Numbered menu on stderr, so stdout stays pipeable.

    Returns None if the user aborts, which the caller treats as a clean exit
    rather than an error — changing your mind is not a failure.
    """
    stream = stream or sys.stdin
    out = prompt_stream or sys.stderr

    if not programs:
        return None
    if len(programs) == 1:
        return programs[0]

    for index, program in enumerate(programs, 1):
        suffix = f"  — {program.description}" if program.description else ""
        print(f"  {index:>2}. {program.name}{suffix}", file=out)
    print("", file=out)

    while True:
        print(f"Select 1-{len(programs)} (or blank to cancel): ", end="", file=out, flush=True)
        raw = stream.readline()
        if not raw:
            return None
        raw = raw.strip()
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(programs):
            return programs[int(raw) - 1]
        print(f"  not a choice: {raw!r}", file=out)


def choose_name(kind: str, names: list[str], *, stream=None, prompt_stream=None) -> str | None:
    """A numbered menu over plain names, for hosts and groups.

    Separate from `choose` because a Program carries a description worth showing
    and a name does not, and pretending otherwise would mean a blank column.
    """
    stream = stream or sys.stdin
    out = prompt_stream or sys.stderr

    if not names:
        return None
    if len(names) == 1:
        return names[0]

    print(f"Which {kind}?", file=out)
    for index, name in enumerate(names, 1):
        print(f"  {index:>2}. {name}", file=out)
    print("", file=out)

    while True:
        print(f"Select 1-{len(names)} (or blank to cancel): ", end="", file=out, flush=True)
        raw = stream.readline()
        if not raw:
            return None
        raw = raw.strip()
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        print(f"  not a choice: {raw!r}", file=out)
