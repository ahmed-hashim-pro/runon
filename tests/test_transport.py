from __future__ import annotations

from pathlib import Path

from runon.inventory import Host
from runon.transport import (
    LocalTransport,
    Result,
    SSHTransport,
    _looks_like_missing_sftp,
    env_prefix,
)

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
        assert env_prefix({"A": "b c"}) == "export A='b c'; "

    def test_a_value_cannot_smuggle_a_command(self):
        assert env_prefix({"A": "x; rm -rf /"}) == "export A='x; rm -rf /'; "

    def test_empty_env_adds_nothing(self):
        assert env_prefix(None) == ""
        assert env_prefix({}) == ""

    def test_ordering_is_stable(self):
        # so a failing command is reproducible from the log
        assert env_prefix({"B": "2", "A": "1"}) == "export A=1 B=2; "

    def test_it_exports_rather_than_prefixing_one_command(self):
        """`A=1 cd dir && ./main.sh` sets A for the cd, and nothing else.

        The command runon sends starts with cd, so a bare assignment prefix
        meant no variable ever reached a remote program — not the parameters,
        not the prompts, not RUNON_HOST.
        """
        assert env_prefix({"A": "1"}).startswith("export ")

    def test_the_variables_actually_reach_a_program_past_a_cd(self, tmp_path):
        """Run it, rather than asserting on the string that was wrong before."""
        import subprocess

        (tmp_path / "p").mkdir()
        entry = tmp_path / "p" / "main.sh"
        entry.write_text('#!/bin/sh\necho "saw=${A:-unset}"\n', encoding="utf-8")
        entry.chmod(0o755)

        command = env_prefix({"A": "1"}) + "cd p && ./main.sh"
        out = subprocess.run(
            ["/bin/sh", "-c", command], cwd=tmp_path, capture_output=True, text=True
        )

        assert out.stdout.strip() == "saw=1"


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
            Host("local", "localhost"), "echo $RUNON_HOST", env={"RUNON_HOST": "abc"}
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


class TestFakeResponses:
    """FakeTransport is public API, so its scripting is a contract."""

    def test_a_scripted_failure_is_returned_for_a_matching_command(self):
        from runon.transport import FakeTransport

        fake = FakeTransport(responses={"migrate": Result("", "", 1, "", "lock held")})
        host = Host(name="web-1", address="web-1.example.com")

        assert fake.run(host, "./migrate.sh").exit_code == 1
        assert fake.run(host, "./migrate.sh").stderr == "lock held"
        # anything unmatched is still a success
        assert fake.run(host, "./smoke.sh").ok

    def test_default_exit_makes_every_command_fail(self):
        from runon.transport import FakeTransport

        fake = FakeTransport(default_exit=1)

        assert not fake.run(Host(name="a", address="a"), "anything").ok

    def test_it_covers_everything_the_remote_path_calls(self):
        """--watch reaches for ssh_argv, which the Transport protocol does not
        declare; rehearsing a watch run used to raise AttributeError here."""
        from runon.transport import FakeTransport

        fake = FakeTransport()
        for method in ("run", "copy", "ssh_argv"):
            assert callable(getattr(fake, method, None)), method


class TestRawValues:
    """runon's own remote paths have to be expanded by the remote shell.

    Everything a user supplies must not be. Both in one prefix, so the test
    that proves the expansion also proves the quoting still holds.
    """

    def _run(self, env, script, home):
        import subprocess

        from runon.transport import env_prefix

        return subprocess.run(
            ["/bin/sh", "-c", env_prefix(env) + script],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        ).stdout

    def test_a_raw_path_is_expanded_by_the_shell(self, tmp_path):
        from runon.transport import Raw

        out = self._run(
            {"RUNON_FUNCTIONS": Raw('"$HOME/.runon"/functions')},
            'echo "$RUNON_FUNCTIONS"',
            tmp_path,
        )

        assert out.strip() == f"{tmp_path}/.runon/functions"

    def test_a_quoted_tilde_would_not_have_been(self, tmp_path):
        """What the bug looked like: `.: cannot open ~/.runon/functions/say.sh`."""
        out = self._run(
            {"RUNON_FUNCTIONS": "~/.runon/functions"}, 'echo "$RUNON_FUNCTIONS"', tmp_path
        )

        assert out.strip() == "~/.runon/functions"

    def test_a_user_value_is_still_only_a_value(self, tmp_path):
        out = self._run(
            {"RUNON_VAR_ROLE": "a; echo INJECTED"}, 'echo "role=$RUNON_VAR_ROLE"', tmp_path
        )

        assert "INJECTED" not in out.replace("role=a; echo INJECTED", "")
        assert out.strip() == "role=a; echo INJECTED"

    def test_both_kinds_in_one_command(self, tmp_path):
        from runon.transport import Raw

        out = self._run(
            {
                "RUNON_FUNCTIONS": Raw('"$HOME/.runon"/functions'),
                "RUNON_PARAM_MSG": "$HOME is not expanded here",
            },
            'echo "$RUNON_FUNCTIONS"; echo "$RUNON_PARAM_MSG"',
            tmp_path,
        )

        lines = out.strip().splitlines()
        assert lines[0] == f"{tmp_path}/.runon/functions"
        assert lines[1] == "$HOME is not expanded here"

    def test_the_functions_path_a_remote_run_actually_sends(self, workspace, tmp_path):
        from runon import runner
        from runon.inventory import Host
        from runon.transport import FakeTransport

        fake = FakeTransport()
        runner.run_program(
            fake, Host("web-1", "10.0.0.1"), workspace, workspace.program("hello-world")
        )

        functions = fake.envs[-1]["RUNON_FUNCTIONS"]
        assert functions == '"$HOME/.runon"/functions'
        assert "~" not in functions
