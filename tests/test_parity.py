"""The capabilities carried over from the tool this replaced."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import tty

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
        # read off the parser, so adding a command cannot leave completion behind
        subcommands = next(
            action for action in cli.build_parser()._actions if hasattr(action, "choices")
            and action.choices and action.dest == "scope"
        )
        for scope in subcommands.choices:
            assert scope in script("bash"), scope


class TestNamePicker:
    def test_selects_by_number(self):
        chosen = choose_name(
            "host", ["a", "b"], stream=tty("2\n"), prompt_stream=io.StringIO()
        )
        assert chosen == "b"

    def test_a_single_option_needs_no_prompt(self):
        chosen = choose_name(
            "group", ["only"], stream=io.StringIO(), prompt_stream=io.StringIO()
        )
        assert chosen == "only"

    def test_host_and_group_are_optional_now(self):
        parser = cli.build_parser()
        # the original prompted rather than requiring the flag
        assert parser.parse_args(["host", "run-program", "--program", "p"]).host is None
        assert parser.parse_args(["group", "run-program", "--program", "p"]).group is None


class TestVersion:
    """0.2.0 shipped with pyproject saying 0.2.0 and --version saying 0.1.0."""

    def test_the_reported_version_matches_the_installed_package(self):
        from importlib.metadata import version

        import runon

        assert runon.__version__ == version("runon")

    def test_it_matches_pyproject(self):
        import tomllib
        from pathlib import Path

        import runon

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text())["project"]["version"]
        assert runon.__version__ == declared

    def test_the_cli_prints_it(self, capsys):
        import runon

        with pytest.raises(SystemExit):
            cli.main(["--version"])
        assert runon.__version__ in capsys.readouterr().out


class TestCompletionRuns:
    """`bash -n` only proves the script parses. This runs it, in bash and zsh.

    Fish is not driven here — its completions need a real fish session — so
    that one is covered only by the script being generated at all.

    A broken f-string escape in the heredoc produces a script that is valid
    shell and completes nothing, which is the failure worth catching.
    """

    def _shim(self, tmp_path):
        """A `runon` on PATH answering from this workspace.

        The completion scripts shell out to the real command, so the test needs
        one — without requiring the package installed in the environment
        running pytest.
        """
        bindir = tmp_path / "bin"
        if not bindir.is_dir():
            bindir.mkdir()
            shim = bindir / "runon"
            shim.write_text(
                "#!/bin/sh\n"
                f"exec {sys.executable} -c "
                "'import sys;from runon.cli import main;sys.exit(main())' \"$@\"\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
        return bindir

    def _complete(self, tmp_path, words: list[str]) -> list[str]:
        import os
        import shutil

        bash = shutil.which("bash")
        assert bash, "bash is needed to test bash completion"

        script_file = tmp_path / "runon.bash"
        script_file.write_text(script("bash"), encoding="utf-8")
        bindir = self._shim(tmp_path)

        quoted = " ".join(f'"{w}"' for w in words)
        driver = (
            f"source {script_file}\n"
            f"COMP_WORDS=({quoted})\n"
            f"COMP_CWORD={len(words) - 1}\n"
            "COMPREPLY=()\n"
            "_runon\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n'
        )
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "RUNON_HOME": str(tmp_path / "home"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        out = subprocess.run(
            [bash, "-c", driver], capture_output=True, text=True, env=env, cwd=tmp_path
        )
        return [line for line in out.stdout.split("\n") if line]

    def _complete_zsh(self, tmp_path, words: list[str]) -> list[str]:
        import os
        import shutil

        zsh = shutil.which("zsh")
        if not zsh:
            pytest.skip("zsh is not installed here")

        script_file = tmp_path / "_runon"
        script_file.write_text(script("zsh"), encoding="utf-8")
        bindir = self._shim(tmp_path)

        quoted = " ".join(f'"{w}"' for w in words)
        driver = (
            f"words=({quoted})\n"
            f"CURRENT={len(words)}\n"
            # the real compadd needs a running completion system; this stands in
            'compadd(){ print -r -- "${@:#-*}"; }\n'
            f"source {script_file}\n"
        )
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "RUNON_HOME": str(tmp_path / "home"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        out = subprocess.run(
            [zsh, "-f", "-c", driver], capture_output=True, text=True, env=env, cwd=tmp_path
        )
        return [line for line in out.stdout.split("\n") if line]

    def test_scopes_come_first(self, tmp_path):
        assert "doctor" in self._complete(tmp_path, ["runon", ""])

    def test_zsh_completes_program_names_after_the_verb(self, tmp_path):
        assert "hello-world" in self._complete_zsh(
            tmp_path, ["runon", "local", "run-program", ""]
        )

    def test_zsh_completes_layouts(self, tmp_path):
        assert "split" in self._complete_zsh(tmp_path, ["runon", "local", "run-layout", ""])

    def test_program_names_follow_the_verb(self, tmp_path):
        # the whole point: nobody wants to type --program first
        assert "hello-world" in self._complete(
            tmp_path, ["runon", "local", "run-program", ""]
        )

    def test_program_names_follow_the_flag_too(self, tmp_path):
        assert "hello-world" in self._complete(
            tmp_path, ["runon", "local", "run-program", "--program", ""]
        )

    def test_a_remote_verb_completes_programs(self, tmp_path):
        assert "hello-world" in self._complete(
            tmp_path, ["runon", "group", "copy-run-program", ""]
        )

    def test_layouts_are_completed(self, tmp_path):
        assert "split" in self._complete(tmp_path, ["runon", "local", "run-layout", ""])

    def test_nothing_that_is_not_a_name_is_offered(self, tmp_path):
        # `list programs` prints its hints on stderr, so a first run cannot
        # offer the words of an error message as program names
        assert self._complete(tmp_path, ["runon", "local", "run-program", ""]) == [
            "hello-world"
        ]
