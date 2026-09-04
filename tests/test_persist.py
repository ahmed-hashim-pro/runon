"""Connection reuse: what makes repeated commands stop asking."""

from __future__ import annotations

import stat

import pytest

from runon import cli
from runon.inventory import Host
from runon.transport import DEFAULT_PERSIST, SSHTransport, _socket_dir

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


class TestPersistFlag:
    def _args(self, value):
        import argparse

        return argparse.Namespace(persist=value)

    def test_default_is_on(self):
        assert cli._persist_for(self._args(DEFAULT_PERSIST)) == DEFAULT_PERSIST

    @pytest.mark.parametrize("value", ["no", "none", "off", "0", "NO", None])
    def test_disabling_it(self, value):
        assert cli._persist_for(self._args(value)) is None

    def test_a_custom_duration_survives(self):
        assert cli._persist_for(self._args("15m")) == "15m"

    def test_the_flag_is_on_both_remote_scopes(self):
        parser = cli.build_parser()
        for scope, selector in (("host", "--host"), ("group", "--group")):
            args = parser.parse_args(
                [scope, selector, "x", "--persist", "5m", "run-program", "--program", "p"]
            )
            assert args.persist == "5m"
