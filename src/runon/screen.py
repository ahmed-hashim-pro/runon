"""A full-screen picker, written against the terminal directly.

runon has no runtime dependencies, and a picker is not a good reason to acquire
one: the alternative is a library that must be present on every machine anyone
installs this on, to draw a list. So this talks to the terminal itself.

It is deliberately small. Arrows and typing, one escape sequence parser, and a
`finally` that puts the terminal back however it leaves. Anything it cannot do
— no tty, no termios, an unfamiliar terminal — falls back to the numbered menu,
which is why nothing here is allowed to be load-bearing.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

ESC = "\x1b"
CSI = "\x1b["

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
ENTER, BACKSPACE, CANCEL = "enter", "backspace", "cancel"

_SEQUENCES = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}


@dataclass(frozen=True)
class Choice:
    """One row. `key` is what the caller gets back; the rest is for reading."""

    key: str
    label: str
    category: str = "uncategorized"
    description: str = ""
    details: str = ""
    tags: tuple[str, ...] = ()
    note: str = ""


def available(stream=None) -> bool:
    """Whether a full-screen picker can be drawn at all."""
    stream = stream or sys.stdin
    try:
        import termios  # noqa: F401
    except ImportError:
        return False
    try:
        return bool(stream.isatty() and sys.stderr.isatty())
    except (ValueError, OSError):
        return False


class RawTerminal:
    """cbreak mode, restored however the block exits.

    A crash that leaves the terminal in raw mode leaves the user with a shell
    that does not echo, which is a far worse failure than not having a picker.
    """

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        self._saved = None

    def __enter__(self):
        import termios
        import tty

        self.fd = self.stream.fileno()
        self._saved = termios.tcgetattr(self.fd)
        # cbreak both delivers keys unbuffered and turns echo off, which is
        # what stops a keypress printing into the middle of the drawing.
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc) -> None:
        import termios

        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
        return None

    def key(self) -> str:
        """One keypress, with escape sequences collapsed to a name.

        A bare Escape and the start of an arrow sequence are the same first
        byte, so this reads what is already buffered rather than waiting: a
        terminal sends the rest of an arrow at once, and a person pressing
        Escape sends nothing more.
        """
        import select

        char = os.read(self.fd, 1).decode(errors="replace")
        if char in ("\r", "\n"):
            return ENTER
        if char in ("\x7f", "\b"):
            return BACKSPACE
        if char == "\x03":  # Ctrl-C
            return CANCEL
        if char != ESC:
            return char

        ready, _, _ = select.select([self.fd], [], [], 0.05)
        if not ready:
            return CANCEL
        if os.read(self.fd, 1) != b"[":
            return CANCEL

        # One byte at a time up to the sequence's final character, which is
        # the only one that names the key. Reading a fixed-size block instead
        # swallows whatever arrived in the same burst, dropping a keypress
        # whenever two arrive together — autorepeat, or a paste.
        for _ in range(16):
            char = os.read(self.fd, 1).decode(errors="replace")
            if not char:
                return ""
            if "@" <= char <= "~":
                return _SEQUENCES.get(char, "")
        return ""


def choose(
    choices: list[Choice],
    *,
    title: str = "Select",
    recent: list[str] | None = None,
    stream=None,
    out=None,
) -> str | None:
    """Draws the picker and returns the chosen key, or None if cancelled."""
    out = out or sys.stderr
    recent = [r for r in (recent or []) if any(c.key == r for c in choices)]
    state = _State(choices, recent, title)

    with RawTerminal(stream) as term:
        try:
            out.write(f"{CSI}?25l")  # hide the cursor while we draw
            while True:
                out.write(state.render())
                out.flush()
                action = state.handle(term.key())
                if action is not None:
                    return None if action is _CANCELLED else action
        except KeyboardInterrupt:
            # cbreak leaves ISIG on, so Ctrl-C arrives as a signal rather than
            # as the byte the key parser is ready for. Both mean "stop".
            return None
        finally:
            out.write(f"{CSI}?25h{CSI}0m\n")
            out.flush()


_CANCELLED = object()


class _State:
    def __init__(self, choices: list[Choice], recent: list[str], title: str) -> None:
        self.choices = choices
        self.recent = recent
        self.title = title
        self.filter = ""
        self.selected = 0
        self.tab = 0
        self._drawn = 0

        present = sorted({c.category for c in choices})
        self.tabs = ["All"] + (["Recent"] if recent else []) + present

    # -- what is on screen ---------------------------------------------------

    def visible(self) -> list[Choice]:
        tab = self.tabs[self.tab]
        if tab == "Recent":
            by_key = {c.key: c for c in self.choices}
            rows = [by_key[k] for k in self.recent if k in by_key]
        elif tab == "All":
            rows = list(self.choices)
        else:
            rows = [c for c in self.choices if c.category == tab]
        if self.filter:
            needle = self.filter.lower()
            rows = [c for c in rows if needle in c.label.lower() or needle in c.key.lower()]
        return rows

    def render(self) -> str:
        rows = self.visible()
        self.selected = max(0, min(self.selected, len(rows) - 1)) if rows else 0
        width = shutil.get_terminal_size((80, 24)).columns

        lines = [f"{CSI}1m{self.title}{CSI}0m"]
        if len(self.tabs) > 1:
            lines.append(" ".join(self._tab(i, name) for i, name in enumerate(self.tabs)))
        lines.append("")

        if not rows:
            lines.append(f"{CSI}2m  nothing matches {self.filter!r}{CSI}0m")
        for index, choice in enumerate(rows):
            lines.append(self._row(choice, index == self.selected))

        lines.append("")
        lines.extend(self._preview(rows[self.selected] if rows else None, width))
        lines.append(
            f"{CSI}2m  ↑↓ move   ←→ category   type to filter   ⏎ select   esc cancel{CSI}0m"
        )
        if self.filter:
            lines.append(f"  filter: {self.filter}")

        # Every line, not just the rows: one line wider than the terminal
        # wraps onto two, and the cursor arithmetic below counts lines, so a
        # single long description would put the whole redraw out by one.
        lines = [_clip(line, width) for line in lines]

        # Redraw in place: move up over what was drawn last time, then clear to
        # the end of the screen. Scrolling the whole list past you on every
        # keypress is what makes a naive picker unusable.
        rewind = f"{CSI}{self._drawn}A" if self._drawn else ""
        self._drawn = len(lines)
        return rewind + f"{CSI}0J" + "\n".join(lines) + "\n"

    def _tab(self, index: int, name: str) -> str:
        if index == self.tab:
            return f"{CSI}7m {name} {CSI}0m"
        return f"{CSI}2m {name} {CSI}0m"

    def _row(self, choice: Choice, selected: bool) -> str:
        marker = "›" if selected else " "
        label = _highlight(choice.label, self.filter, bold=selected)
        note = f" {CSI}33m{choice.note}{CSI}0m" if choice.note else ""
        line = f" {marker} {label}{note}"
        if choice.description:
            line += f"  {CSI}2m— {choice.description}{CSI}0m"
        return line

    def _preview(self, choice: Choice | None, width: int) -> list[str]:
        if choice is None:
            return [""]
        body = choice.details or choice.description or ""
        lines = [f"{CSI}2m  {_clip_plain(body, width - 4)}{CSI}0m" if body else ""]
        if choice.tags:
            lines.append(f"{CSI}2m  tags: {', '.join(choice.tags)}{CSI}0m")
        return lines

    # -- what the keys do ----------------------------------------------------

    def handle(self, key: str):
        rows = self.visible()
        if key == CANCEL:
            return _CANCELLED
        if key == ENTER:
            return rows[self.selected].key if rows else _CANCELLED
        if key == UP:
            self.selected = (self.selected - 1) % len(rows) if rows else 0
        elif key == DOWN:
            self.selected = (self.selected + 1) % len(rows) if rows else 0
        elif key in (LEFT, RIGHT):
            step = -1 if key == LEFT else 1
            self.tab = (self.tab + step) % len(self.tabs)
            self.selected = 0
        elif key == BACKSPACE:
            self.filter = self.filter[:-1]
            self.selected = 0
        elif key and key.isprintable():
            self.filter += key
            self.selected = 0
        return None


def _highlight(text: str, needle: str, *, bold: bool) -> str:
    base = f"{CSI}1m" if bold else ""
    if not needle:
        return f"{base}{text}{CSI}0m"
    at = text.lower().find(needle.lower())
    if at < 0:
        return f"{base}{text}{CSI}0m"
    end = at + len(needle)
    return (
        f"{base}{text[:at]}{CSI}0m{CSI}4m{text[at:end]}{CSI}0m{base}{text[end:]}{CSI}0m"
    )


def _clip_plain(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _clip(line: str, width: int) -> str:
    """Truncates on visible length, ignoring the escape sequences in between."""
    visible = 0
    out = []
    index = 0
    while index < len(line):
        if line[index] == ESC:
            end = line.find("m", index)
            if end == -1:
                break
            out.append(line[index : end + 1])
            index = end + 1
            continue
        if visible >= width - 1:
            out.append("…")
            break
        out.append(line[index])
        visible += 1
        index += 1
    return "".join(out) + f"{CSI}0m"
