"""Connection reuse: what makes repeated commands stop asking."""

from __future__ import annotations

import argparse
import pathlib
import stat

import pytest

from runon import cli
from runon.inventory import Host
from runon.transport import (
    DEFAULT_PERSIST,
    MAX_SOCKET_DIR,
    SSHTransport,
    _socket_dir,
    control_path,
)

HOST = Host(name="h", address="example.com")


def argv_for(**kw) -> str:
    return " ".join(SSHTransport(**kw)._base(HOST, "ssh"))


class TestMultiplexing:
    def test_on_by_default(self):
        argv = argv_for()
        assert "ControlMaster=auto" in argv
        assert f"ControlPersist={DEFAULT_PERSIST}" in argv

    def test_can_be_turned_off(self):
        assert "ControlMaster" not in argv_for(persist=None)

    def test_the_duration_is_passed_through(self):
        assert "ControlPersist=10m" in argv_for(persist="10m")

    # A unix socket path is capped near 104 characters, and a long home
    # directory plus a long user@host would blow past it. %C is a hash.
    def test_the_socket_path_is_hashed_not_spelled_out(self):
        argv = argv_for()
        assert "%C" in argv
        assert "example.com" not in argv.split("ControlPath=")[1].split()[0]

    def test_scp_reuses_the_same_connection(self):
        # otherwise copy-run-program authenticates twice per host
        assert "ControlMaster=auto" in " ".join(SSHTransport()._base(HOST, "scp"))


class TestSocketDirectory:
    def test_nobody_else_can_reach_the_sockets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / "home"))

        directory = _socket_dir()

        # Anyone who can reach one of these sockets can use the authenticated
        # connection behind it without knowing any credential.
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    def test_it_is_created_on_demand(self, tmp_path, monkeypatch):
        home = tmp_path / "nothing-here-yet"
        monkeypatch.setenv("RUNON_HOME", str(home))

        assert _socket_dir().is_dir()

    def test_it_is_reused_not_recreated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / "home"))
        assert _socket_dir() == _socket_dir()


class TestControlPathAlwaysFits:
    """Multiplexing is a speed-up, so it may never be why a command fails.

    A home directory long enough to push the socket past the unix-socket cap
    used to fail *every* remote command with "ControlPath too long", and an
    unwritable RUNON_HOME failed them with a bare errno. Both are real: a
    corporate machine hands out homes like /home/corp.example.com/first.last,
    which on its own is most of the budget.
    """

    def test_a_short_home_is_still_preferred(self, monkeypatch):
        # A real short home, not tmp_path: pytest's own tmp_path is routinely
        # longer than the whole budget, so this would skip on most machines
        # and the preferred branch would go untested where it matters.
        import shutil
        import tempfile

        home = pathlib.Path(tempfile.mkdtemp(prefix="rh", dir="/tmp"))
        monkeypatch.setenv("RUNON_HOME", str(home))
        try:
            assert len(str(home / "sockets")) <= MAX_SOCKET_DIR
            assert control_path() == str(home / "sockets")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_long_home_falls_back_instead_of_failing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / ("d" * 200)))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        chosen = control_path()

        assert chosen is not None
        assert len(chosen) <= MAX_SOCKET_DIR

    def test_an_unwritable_home_falls_back_instead_of_failing(self, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", "/proc/nonexistent/runon")
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        assert control_path() is not None

    def test_the_whole_socket_path_fits_a_unix_socket(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / ("d" * 200)))

        chosen = control_path()

        # %C is 40 hex characters, and ssh binds "<path>.<16 random>" before
        # renaming it, so the bound path is 17 longer than the one we ask for.
        assert len(chosen) + len("/") + 40 + 17 <= 104

    def test_multiplexing_is_dropped_rather_than_breaking_the_command(
        self, monkeypatch
    ):
        monkeypatch.setattr("runon.transport.control_path", lambda: None)

        argv = " ".join(SSHTransport()._base(HOST, "ssh"))

        assert "ControlMaster" not in argv
        assert "ControlPath" not in argv

    def test_a_long_home_still_produces_a_runnable_argv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / ("d" * 200)))

        argv = " ".join(SSHTransport()._base(HOST, "ssh"))

        assert "ControlPath too long" not in argv
        assert "%C" in argv

    def test_the_fallback_is_private_to_this_user(self, tmp_path, monkeypatch):
        import os
        import stat as stat_

        monkeypatch.setenv("RUNON_HOME", str(tmp_path / ("d" * 200)))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        chosen = pathlib.Path(control_path())

        info = chosen.lstat()
        # It lives in a world-writable directory, so anyone who can reach the
        # socket can use the authenticated connection behind it.
        assert stat_.S_IMODE(info.st_mode) == 0o700
        assert info.st_uid == os.getuid()

    def test_a_hostile_symlink_in_tmp_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNON_HOME", str(tmp_path / ("d" * 200)))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        planted = tmp_path / "planted"
        planted.mkdir()
        monkeypatch.setattr(
            "runon.transport._socket_candidates",
            lambda: [tmp_path / "link"],
        )
        (tmp_path / "link").symlink_to(planted)

        # Another user got there first. Better no multiplexing than a socket
        # somewhere they chose.
        assert control_path() is None


class TestPersistFlag:
    def _args(self, value):
        import argparse

        return argparse.Namespace(persist=value)

    def test_default_is_on(self):
        assert cli._persist_for(self._args(DEFAULT_PERSIST)) == DEFAULT_PERSIST

    @pytest.mark.parametrize("value", ["no", "none", "off", "0", "NO", None])
    def test_disabling_it(self, value):
        assert cli._persist_for(self._args(value)) is None

    def test_a_duration_ssh_would_reject_is_refused_here(self):
        # ssh rejects it per host, only after connecting, as "Bad ControlPersist
        # argument" with exit 255 — which reads as the host refusing you.
        from runon.errors import RunonError

        for value in ("banana", "10 m", "-5", "m10", "1x"):
            with pytest.raises(RunonError):
                cli._persist_for(self._args(value))

    def test_every_shape_ssh_accepts_is_allowed(self):
        # OpenSSH's own time format, including the combined form.
        for value in ("30", "60s", "10m", "1h", "2d", "1w", "1h30m", "yes"):
            assert cli._persist_for(self._args(value)) == value

    def test_a_custom_duration_survives(self):
        assert cli._persist_for(self._args("15m")) == "15m"

    def test_the_flag_is_on_both_remote_scopes(self):
        parser = cli.build_parser()
        for scope, selector in (("host", "--host"), ("group", "--group")):
            args = parser.parse_args(
                [scope, selector, "x", "--persist", "5m", "run-program", "--program", "p"]
            )
            assert args.persist == "5m"


class TestTimeout:
    """Every command used to be cut off at an hour with no way to say otherwise.

    A package upgrade or a database migration can genuinely take longer, and
    `timed out after 3600s` does not suggest there is anything to be done.
    """

    def _args(self, value):
        return argparse.Namespace(timeout=value)

    def test_the_default_is_still_an_hour(self):
        from runon.transport import DEFAULT_TIMEOUT

        assert cli._timeout_for(self._args(DEFAULT_TIMEOUT)) == DEFAULT_TIMEOUT

    def test_zero_means_wait(self):
        # subprocess reads None as "no deadline"
        assert cli._timeout_for(self._args(0)) is None

    def test_a_number_of_seconds_is_used(self):
        assert cli._timeout_for(self._args(30)) == 30

    def test_negative_is_refused(self):
        from runon.errors import RunonError

        with pytest.raises(RunonError):
            cli._timeout_for(self._args(-1))

    def test_it_survives_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            ["host", "--host", "h", "run-program", "--program", "p", "--timeout", "90"]
        )

        assert args.timeout == 90

    def test_the_flag_is_on_every_verb_that_runs_something(self):
        parser = cli.build_parser()
        for argv in (
            ["local", "run-program", "--program", "p", "--timeout", "5"],
            ["host", "--host", "h", "run-program", "--program", "p", "--timeout", "5"],
            ["host", "--host", "h", "copy-run-program", "--program", "p", "--timeout", "5"],
            ["group", "--group", "g", "copy-program", "--program", "p", "--timeout", "5"],
            ["host", "--host", "h", "copy", "--local-dir", ".", "--remote-dir", "/t",
             "--timeout", "5"],
        ):
            assert parser.parse_args(argv).timeout == 5
