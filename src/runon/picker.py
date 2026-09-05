"""Choosing a program when you did not name one."""

from __future__ import annotations

import os
import sys

from . import config, screen
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


def choose(
    programs: list[Program], *, stream=None, prompt_stream=None, rich: bool | None = None
) -> Program | None:
    """Numbered menu on stderr, so stdout stays pipeable.

    Returns None if the user aborts, which the caller treats as a clean exit
    rather than an error — changing your mind is not a failure.

    On a real terminal this hands off to the full-screen picker, and falls back
    to the menu below if that cannot draw. The menu stays the thing that always
    works, which is why the picker is allowed to be the part that might not.
    """
    stream = stream or sys.stdin
    out = prompt_stream or sys.stderr

    if not programs:
        return None
    # Before the single-option shortcut, not after: a scripted run should mean
    # the same thing tomorrow, and today's only program is tomorrow's first of
    # three. Auto-selecting is a convenience for a person looking at a menu.
    _require_a_terminal(stream, "--program", [p.name for p in programs])
    if len(programs) == 1:
        return programs[0]

    if rich is None:
        rich = screen.available(stream) and os.environ.get("RUNON_PLAIN") != "1"
    if rich:
        chosen = _choose_richly(programs, stream, out)
        if chosen is not _FELL_BACK:
            return chosen

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


#: Sentinel for "the picker could not draw; use the menu".
_FELL_BACK = object()


def _choose_richly(programs: list[Program], stream, out):
    """Runs the full-screen picker, or gives up quietly.

    Every failure falls back rather than propagating: a terminal that does not
    support this should cost you a plainer menu, not your run.
    """
    choices = []
    for program in programs:
        try:
            meta = program.meta()
        except RunonError:
            # A broken meta.toml is worth an error when you run the program,
            # not when you are still deciding which one to run.
            meta = None
        choices.append(
            screen.Choice(
                key=program.name,
                label=program.name,
                category=(meta.category if meta else "uncategorized"),
                # `description` already falls back to main.sh's first comment,
                # so a program with no meta.toml still says what it does.
                description=program.description,
                details=(meta.details if meta else ""),
                tags=(meta.tags if meta else ()),
                note=_note(meta),
            )
        )

    try:
        picked = screen.choose(
            choices, title="Which program?", recent=config.recent_programs(),
            stream=stream, out=out,
        )
    except Exception:
        return _FELL_BACK
    if picked is None:
        return None
    return next(p for p in programs if p.name == picked)


def _note(meta) -> str:
    if meta is None:
        return ""
    marks = []
    if meta.destructive:
        marks.append("destructive")
    if meta.status != "active":
        marks.append(meta.status)
    return f"[{', '.join(marks)}]" if marks else ""


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
    _require_a_terminal(stream, f"--{kind}", names)
    if len(names) == 1 and not offer_new:
        return names[0]

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
