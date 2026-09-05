"""Choosing a program when you did not name one."""

from __future__ import annotations

import sys

from .errors import RunonError
from .program import Program


def _require_a_terminal(stream, flag: str, choices: list[str]) -> None:
    """A menu is only an answer when someone is there to read it.

    Without this, a run from cron or CI reaches EOF on the first prompt, treats
    it as "cancelled", and exits 0 having done nothing at all — the same silent
    success that an empty --program is refused for.
    """
    if stream.isatty():
        return
    raise RunonError(
        f"{flag} was not given and there is no terminal to ask on.\n"
        f"Pass {flag} explicitly. Choices: {', '.join(choices)}"
    )


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
    _require_a_terminal(stream, "--program", [p.name for p in programs])

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


#: Returned instead of a name when the menu's last entry was chosen.
ADD_NEW = "\x00add-new"


def choose_name(
    kind: str, names: list[str], *, stream=None, prompt_stream=None, offer_new: bool = False
) -> str | None:
    """A numbered menu over plain names, for hosts and groups.

    Separate from `choose` because a Program carries a description worth showing
    and a name does not, and pretending otherwise would mean a blank column.

    With `offer_new`, the last entry adds one instead of picking one: the moment
    you notice a machine is missing is the moment you are looking at the list.
    """
    stream = stream or sys.stdin
    out = prompt_stream or sys.stderr

    if not names and not offer_new:
        return None
    if len(names) == 1 and not offer_new:
        return names[0]
    _require_a_terminal(stream, f"--{kind}", names)

    entries = [*names] + ([f"add a new {kind}…"] if offer_new else [])
    print(f"Which {kind}?", file=out)
    for index, entry in enumerate(entries, 1):
        print(f"  {index:>2}. {entry}", file=out)
    print("", file=out)

    while True:
        print(f"Select 1-{len(entries)} (or blank to cancel): ", end="", file=out, flush=True)
        raw = stream.readline()
        if not raw:
            return None
        raw = raw.strip()
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(entries):
            index = int(raw) - 1
            return ADD_NEW if offer_new and index == len(names) else names[index]
        print(f"  not a choice: {raw!r}", file=out)
