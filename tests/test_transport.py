from __future__ import annotations

from pathlib import Path

from fleetsh.inventory import Host
from fleetsh.transport import LocalTransport, SSHTransport, _env_prefix, _looks_like_missing_sftp

HOST = Host(name="h", address="example.com", user="deploy")
PORTED = Host(name="p", address="example.com", port=2222)


class TestSSHArgv:
    """The command line is the contract with ssh; assert it directly."""

    def test_port_flag_differs_between_ssh_and_scp(self):
        transport = SSHTransport()
        # a long-standing OpenSSH wart: ssh takes -p, scp takes -P
        assert "-p" in transport._base(PORTED, "ssh")
        assert "-P" in transport._base(PORTED, "scp")

    def test_batch_mode_is_always_on(self):
        # without it a missing key hangs on a password prompt instead of failing,
        # which across a group means twenty hung connections
        assert "BatchMode=yes" in " ".join(SSHTransport()._base(HOST, "ssh"))

    def test_no_port_flag_when_none_is_configured(self):
        assert "-p" not in SSHTransport()._base(HOST, "ssh")


class TestEnvPrefix:
    def test_values_are_quoted(self):
        assert _env_prefix({"A": "b c"}) == "A='b c' "

    def test_a_value_cannot_smuggle_a_command(self):
        prefix = _env_prefix({"A": "x; rm -rf /"})
        assert prefix == "A='x; rm -rf /' "

    def test_empty_env_adds_nothing(self):
        assert _env_prefix(None) == ""
        assert _env_prefix({}) == ""

    def test_ordering_is_stable(self):
        # so a failing command is reproducible from the log
        assert _env_prefix({"B": "2", "A": "1"}) == "A=1 B=2 "


class TestSFTPHint:
    """OpenSSH 9 moved scp onto SFTP; the resulting error does not say so."""

    def test_recognises_the_failure(self):
        assert _looks_like_missing_sftp("subsystem request failed on channel 0")

    def test_ignores_unrelated_errors(self):
        assert not _looks_like_missing_sftp("Permission denied (publickey)")


class TestLocalTransport:
    def test_runs_a_command(self):
        result = LocalTransport().run(Host("local", "localhost"), "echo hello")
        assert result.ok
        assert "hello" in result.stdout

    def test_reports_a_failing_exit_code(self):
        assert LocalTransport().run(Host("local", "localhost"), "exit 3").exit_code == 3

    def test_env_reaches_the_command(self):
        result = LocalTransport().run(
            Host("local", "localhost"), "echo $FLEETSH_HOST", env={"FLEETSH_HOST": "abc"}
        )
        assert "abc" in result.stdout

    def test_a_hanging_command_times_out_rather_than_blocking(self):
        result = LocalTransport(timeout=1).run(Host("local", "localhost"), "sleep 30")
        assert result.exit_code == 124
        assert "timed out" in result.stderr

    def test_copies_a_file(self, tmp_path: Path):
        source = tmp_path / "a.txt"
        source.write_text("content")
        destination = tmp_path / "nested" / "b.txt"

        result = LocalTransport().copy(Host("local", "localhost"), source, str(destination))

        assert result.ok
        assert destination.read_text() == "content"

    def test_copies_a_directory(self, tmp_path: Path):
        source = tmp_path / "src"
        (source / "inner").mkdir(parents=True)
        (source / "inner" / "f.txt").write_text("x")
        destination = tmp_path / "dst"

        assert LocalTransport().copy(Host("l", "localhost"), source, str(destination)).ok
        assert (destination / "inner" / "f.txt").read_text() == "x"

    def test_a_failing_copy_returns_the_reason(self, tmp_path: Path):
        result = LocalTransport().copy(
            Host("l", "localhost"), tmp_path / "missing", str(tmp_path / "out")
        )
        assert not result.ok
        assert result.stderr
