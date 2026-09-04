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
