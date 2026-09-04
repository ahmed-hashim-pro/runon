"""The capabilities carried over from the tool this replaced."""

from __future__ import annotations

import subprocess

import pytest

from runon import cli, runner, watch
from runon.completion import script
from runon.doctor import Check, report, run_checks
from runon.errors import ProgramInvalid
from runon.inventory import Host
from runon.picker import choose_name
from runon.transport import FakeTransport, SSHTransport

HOSTS = [Host("web-1", "web-1.example.com"), Host("web-2", "web-2.example.com")]


class TestProgramParameters:
    """Settings that belong to the program, and travel with it."""

    def _program(self, workspace, body: str):
        path = workspace.programs_path / "configured"
        path.mkdir()
        (path / "main.sh").write_text("#!/bin/sh\n# Configured.\necho hi\n")
        (path / "params.toml").write_text(body)
        return workspace.program("configured")

    def test_values_reach_the_program_as_env(self, workspace):
        program = self._program(workspace, 'threshold = 90\nbranch = "main"\ndry = true\n')

        env = runner.program_env(HOSTS[0], program, "/f")

        assert env["RUNON_PARAM_THRESHOLD"] == "90"
        assert env["RUNON_PARAM_BRANCH"] == "main"
        assert env["RUNON_PARAM_DRY"] == "true"

    def test_a_program_without_params_still_works(self, workspace):
        assert workspace.program("hello-world").params() == {}

    def test_a_program_parameter_beats_a_host_variable(self, workspace):
        program = self._program(workspace, 'role = "from-program"\n')
        host = Host("h", "h", vars={"role": "from-host"})

        # the program is the more specific statement of intent
        assert runner.program_env(host, program, "/f")["RUNON_PARAM_ROLE"] == "from-program"
        assert runner.program_env(host, program, "/f")["RUNON_VAR_ROLE"] == "from-host"

    def test_broken_toml_names_the_file(self, workspace):
        program = self._program(workspace, "threshold = \n")
        with pytest.raises(ProgramInvalid) as excinfo:
            program.params()
        assert "params.toml" in str(excinfo.value)

    def test_a_nested_value_is_refused_with_a_reason(self, workspace):
        program = self._program(workspace, "[nested]\na = 1\n")
        with pytest.raises(ProgramInvalid) as excinfo:
            program.params()
        # env vars are flat; silently dropping it would be worse
        assert "environment variables" in str(excinfo.value)

    def test_params_travel_with_the_program(self, workspace):
        program = self._program(workspace, "threshold = 90\n")
        fake = FakeTransport()

        runner.copy_program(fake, HOSTS[0], workspace, program)

        # the whole directory is copied, so params.toml goes with it
        assert str(program.path) in [local for _, local, _ in fake.copies]


class TestWatch:
    """One pane per host, which is what Yakuake was doing in the original."""

    def test_a_command_per_host(self):
        commands = watch.build_commands(HOSTS, SSHTransport().ssh_argv(), "echo hi")

        assert len(commands) == 2
        assert commands[0][-2:] == ["web-1.example.com", "echo hi"]

    def test_a_terminal_is_forced(self):
        # without -t a program that prompts or colours output behaves
        # differently in a pane than it does by hand
        assert "-t" in SSHTransport().ssh_argv()

    @pytest.mark.parametrize(
        "label,expected",
        [("disk-report", "runon-disk-report"), ("a.b:c", "runon-a-b-c"), ("x" * 100, None)],
    )
    def test_session_names_are_safe_for_tmux(self, label, expected):
        name = watch.session_name(label)
        # tmux reads . and : as address separators
        assert "." not in name and ":" not in name
        assert len(name) <= 60
        if expected:
            assert name == expected

    def test_panes_are_tiled_and_kept_open(self, monkeypatch):
        # The fake runner means no tmux is actually driven, but the guard in
        # open_panes still runs — and CI machines do not all have tmux.
        monkeypatch.setattr(watch, "tmux_available", lambda: True)
        calls = []
        watch.open_panes(
            HOSTS,
            watch.build_commands(HOSTS, ["ssh"], "echo hi"),
            label="t",
            attach=False,
            runner=lambda argv, **kw: calls.append(argv),
        )
        flat = [" ".join(c) for c in calls]

        # a pane that vanishes takes the error with it
        assert any("remain-on-exit on" in c for c in flat)
        assert any("select-layout" in c and "tiled" in c for c in flat)
        assert not any("attach-session" in c for c in flat)

    def test_it_says_what_to_install_when_tmux_is_absent(self, monkeypatch):
        from runon.errors import RunonError

        monkeypatch.setattr(watch, "tmux_available", lambda: False)
        with pytest.raises(RunonError) as excinfo:
            watch.open_panes(HOSTS, [["ssh"]] * 2, label="t", attach=False)
        assert "tmux" in str(excinfo.value)


class TestDoctor:
    def test_reports_what_is_present(self):
        names = {c.name for c in run_checks()}
        assert {"ssh", "scp", "tmux", "ssh-agent", "ssh-copy-id"} <= names

    def test_missing_optional_tools_are_not_a_failure(self, capsys):
        checks = [Check("tmux", False, "not found", required=False)]
        assert report(checks, stream=__import__("sys").stdout) == 0

    def test_a_missing_required_tool_is(self, capsys):
        checks = [Check("ssh", False, "not found", required=True)]
        assert report(checks, stream=__import__("sys").stdout) == 1


class TestCompletion:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_a_script_is_produced(self, shell):
        assert "runon" in script(shell)

    def test_the_bash_script_is_valid_shell(self, tmp_path):
        path = tmp_path / "c.bash"
        path.write_text(script("bash"))
        # a completion script that does not parse silently does nothing
        assert subprocess.run(["bash", "-n", str(path)]).returncode == 0

    def test_every_scope_is_offered(self):
        for scope in ("local", "host", "group", "list", "init", "doctor"):
            assert scope in script("bash")


class TestNamePicker:
    def test_selects_by_number(self):
        import io

        chosen = choose_name(
            "host", ["a", "b"], stream=io.StringIO("2\n"), prompt_stream=io.StringIO()
        )
        assert chosen == "b"

    def test_a_single_option_needs_no_prompt(self):
        import io

        chosen = choose_name(
            "group", ["only"], stream=io.StringIO(), prompt_stream=io.StringIO()
        )
        assert chosen == "only"

    def test_host_and_group_are_optional_now(self):
        parser = cli.build_parser()
        # the original prompted rather than requiring the flag
        assert parser.parse_args(["host", "run-program", "--program", "p"]).host is None
        assert parser.parse_args(["group", "run-program", "--program", "p"]).group is None
