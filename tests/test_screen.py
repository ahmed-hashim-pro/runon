"""The full-screen picker, driven through a real terminal.

Nothing here can be tested with a fake stream: raw mode needs a tty, and the
behaviour under test is what happens when keys arrive.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

import pytest
from pty_driver import drive

from runon import screen

PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    from runon import screen

    choices = [screen.Choice(key=n, label=n, category=c, description=d) for n, c, d in [
        ("db-backup",   "data",   "Dump and upload"),
        ("deploy",      "deploy", "Ship the current build"),
        ("disk-report", "checks", "Check free space"),
        ("rollback",    "deploy", "Put the last build back"),
    ]]
    print("PICKED=" + str(screen.choose(choices, recent=["deploy"])))
    """
)


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    src = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    path = tmp_path_factory.mktemp("probe") / "probe.py"
    path.write_text(PROBE.format(src=src), encoding="utf-8")
    return str(path)


def pick(probe, *keys: bytes) -> str | None:
    out = drive([sys.executable, probe], list(keys), env={"TERM": "xterm", "COLUMNS": "90"})
    match = re.search(r"PICKED=(\S+)", out)
    assert match, f"the picker never returned. It drew:\n{out[-1500:]}"
    return None if match.group(1) == "None" else match.group(1)


class TestKeys:
    def test_enter_takes_the_highlighted_row(self, probe):
        assert pick(probe, b"\r") == "db-backup"

    def test_arrows_move(self, probe):
        assert pick(probe, b"\x1b[B\x1b[B\r") == "disk-report"

    def test_up_wraps_around(self, probe):
        assert pick(probe, b"\x1b[A\r") == "rollback"

    def test_typing_filters(self, probe):
        assert pick(probe, b"roll", b"\r") == "rollback"

    def test_backspace_unfilters(self, probe):
        assert pick(probe, b"zzz", b"\x7f\x7f\x7f", b"dep", b"\r") == "deploy"

    def test_right_moves_to_the_next_category(self, probe):
        # Recent is the second tab when there is a recent program
        assert pick(probe, b"\x1b[C\r") == "deploy"

    def test_escape_cancels(self, probe):
        assert pick(probe, b"\x1b") is None

    def test_ctrl_c_cancels(self, probe):
        assert pick(probe, b"\x03") is None

    def test_a_modified_arrow_is_read_to_its_end(self, probe):
        """A longer sequence must be consumed whole.

        Ctrl-Up is ESC [ 1 ; 5 A. A parser that stops early leaves ";5A" in the
        buffer, and those become filter characters.
        """
        assert pick(probe, b"\x1b[1;5B", b"\r") == "deploy"

    def test_a_burst_of_keys_loses_none_of_them(self, probe):
        """Two arrows and Enter arriving together.

        Reading a fixed block after ESC swallowed whatever followed in the same
        burst, which drops a keypress on autorepeat or a paste.
        """
        assert pick(probe, b"\x1b[B\x1b[B\r") == "disk-report"


class TestItLeavesTheTerminalAlone:
    def test_the_terminal_is_restored_even_when_it_raises(self):
        """Raw mode outliving the picker leaves the user with no echo."""
        import termios

        master, slave = os.openpty()
        stream = os.fdopen(slave, "rb", buffering=0)
        before = termios.tcgetattr(slave)

        with pytest.raises(RuntimeError), screen.RawTerminal(stream):
            raise RuntimeError("boom")

        # ECHO and ICANON specifically: the kernel also flips PENDIN, which is
        # its own bookkeeping and not something the picker set.
        mask = termios.ECHO | termios.ICANON
        assert termios.tcgetattr(slave)[3] & mask == before[3] & mask
        os.close(master)

    def test_echo_is_off_while_it_draws(self):
        """A keypress must not print into the middle of what is being drawn.

        cbreak provides this today; the test pins the property rather than the
        call, so swapping how raw mode is entered cannot quietly lose it.
        """
        import termios

        master, slave = os.openpty()
        stream = os.fdopen(slave, "rb", buffering=0)
        with screen.RawTerminal(stream):
            assert not termios.tcgetattr(slave)[3] & termios.ECHO
        assert termios.tcgetattr(slave)[3] & termios.ECHO
        os.close(master)


class TestFallingBack:
    """Nothing here is allowed to be load-bearing.

    A terminal the picker cannot draw on should cost you a plainer menu, not
    your run.
    """

    def test_it_declines_when_there_is_no_terminal(self):
        import io

        assert not screen.available(io.StringIO())

    def test_a_picker_that_raises_falls_back_to_the_menu(self, tmp_path, monkeypatch):
        from conftest import tty

        from runon import picker
        from runon.program import Program

        programs = [Program("alpha", tmp_path / "alpha"), Program("beta", tmp_path / "beta")]
        monkeypatch.setattr(
            picker.screen, "available", lambda *_: True
        )
        monkeypatch.setattr(
            picker.screen, "choose", lambda *a, **k: (_ for _ in ()).throw(OSError("no tty"))
        )
        monkeypatch.delenv("RUNON_PLAIN", raising=False)

        import io

        chosen = picker.choose(
            programs, stream=tty("2\n"), prompt_stream=io.StringIO()
        )

        assert chosen.name == "beta"

    def test_runon_plain_skips_the_picker_entirely(self, tmp_path, monkeypatch):
        import io

        from conftest import tty

        from runon import picker
        from runon.program import Program

        monkeypatch.setattr(picker.screen, "available", lambda *_: True)
        monkeypatch.setattr(
            picker.screen, "choose", lambda *a, **k: pytest.fail("drew despite RUNON_PLAIN")
        )
        monkeypatch.setenv("RUNON_PLAIN", "1")

        chosen = picker.choose(
            [Program("alpha", tmp_path / "alpha"), Program("beta", tmp_path / "beta")],
            stream=tty("1\n"),
            prompt_stream=io.StringIO(),
        )

        assert chosen.name == "alpha"

    def test_a_closed_stream_is_not_a_crash(self):
        import io

        stream = io.StringIO()
        stream.close()
        assert not screen.available(stream)


class TestDrawing:
    def _state(self, **kw):
        choices = [
            screen.Choice(key="alpha", label="alpha", category="one", description="first"),
            screen.Choice(key="beta", label="beta", category="two", note="[destructive]"),
        ]
        return screen._State(choices, kw.get("recent", []), "Which?")

    def test_the_selected_row_is_marked(self):
        assert "› " in self._state().render()

    def test_a_note_is_shown(self):
        assert "[destructive]" in self._state().render()

    def test_a_recent_tab_appears_only_when_there_is_one(self):
        assert "Recent" not in self._state().render()
        assert "Recent" in self._state(recent=["alpha"]).render()

    def test_a_filter_matching_nothing_says_so(self):
        state = self._state()
        state.handle("z")
        assert "nothing matches" in state.render()

    def test_long_rows_are_clipped_to_the_width(self, monkeypatch):
        monkeypatch.setattr(
            shutil, "get_terminal_size", lambda *_: os.terminal_size((30, 24))
        )
        choice = screen.Choice(key="x", label="x" * 200, description="y" * 200)
        state = screen._State([choice], [], "t")

        widest = max(len(_visible(line)) for line in state.render().split("\n"))

        # a row wider than the terminal wraps, and wrapping breaks the
        # cursor arithmetic that redraws in place
        assert widest <= 30


def _visible(line: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line)


class TestTheRelatedField:
    """meta.toml accepted `related` and nothing ever showed it.

    write_meta would even write one for you, from the new-program interview,
    so a program could declare a field and get silence back.
    """

    def _render(self, **kw):
        state = screen._State([screen.Choice(key="a", label="a", **kw)], [], "t")
        return state.render()

    def test_related_reaches_the_preview(self):
        assert "see also: rollback, verify" in self._render(related=("rollback", "verify"))

    def test_nothing_is_drawn_when_there_are_none(self):
        assert "see also" not in self._render()

    def test_it_sits_alongside_tags(self):
        drawn = self._render(tags=("api",), related=("rollback",))

        assert "tags: api" in drawn and "see also: rollback" in drawn
