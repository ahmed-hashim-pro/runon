"""The capabilities carried over from the tool this replaced."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import tty

from runon import cli, completion, config, runner, watch
from runon.completion import script
from runon.doctor import Check, report, run_checks
from runon.errors import ProgramInvalid, RunonError
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

    def test_a_single_option_needs_no_prompt_on_a_terminal(self):
        chosen = choose_name("group", ["only"], stream=tty(), prompt_stream=io.StringIO())
        assert chosen == "only"

    def test_but_a_script_still_has_to_say_which(self):
        # one group today, two tomorrow, and the same cron line either way
        with pytest.raises(RunonError):
            choose_name("group", ["only"], stream=io.StringIO(), prompt_stream=io.StringIO())

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


class TestInstallingCompletion:
    """`runon completion --install` — because a script you have to place
    yourself is a script most people never place."""

    @pytest.fixture(autouse=True)
    def _own_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        # no writable system directory unless a test says so
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", ())

    def test_bash_lands_where_bash_looks(self, tmp_path):
        path, remaining = completion.install("bash")

        assert path == tmp_path / "home/.local/share/bash-completion/completions/runon"
        assert path.read_text().startswith("# runon bash completion")
        assert "new shell" in remaining

    def test_fish_needs_nothing_further(self, tmp_path):
        path, remaining = completion.install("fish")

        assert path == tmp_path / "home/.config/fish/completions/runon.fish"
        assert remaining == ""

    def test_zsh_falls_back_and_says_what_to_add(self, tmp_path):
        path, remaining = completion.install("zsh")

        assert path == tmp_path / "home/.zsh/completions/_runon"
        assert "fpath=" in remaining and ".zshrc" in remaining

    def test_zsh_needs_no_rc_edit_when_a_site_directory_is_writable(
        self, tmp_path, monkeypatch
    ):
        site = tmp_path / "site-functions"
        site.mkdir()
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", (str(site),))

        path, remaining = completion.install("zsh")

        assert path == site / "_runon"
        assert "fpath=" not in remaining

    def test_an_unwritable_site_directory_is_skipped(self, tmp_path, monkeypatch):
        site = tmp_path / "readonly"
        site.mkdir(mode=0o500)
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", (str(site),))

        assert completion.install_path("zsh") == tmp_path / "home/.zsh/completions/_runon"

    def test_it_overwrites_an_older_copy(self, tmp_path):
        path, _ = completion.install("bash")
        path.write_text("stale", encoding="utf-8")

        completion.install("bash")

        assert "stale" not in path.read_text()

    def test_the_shell_is_taken_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
        assert completion.default_shell() == "fish"

    def test_an_unknown_shell_is_not_guessed(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/nonesuch")
        assert completion.default_shell() is None

    def test_the_cli_says_so_rather_than_guessing(self, tmp_path, monkeypatch, capsys):
        from test_cli import run

        monkeypatch.setenv("SHELL", "/bin/nonesuch")
        code, _, err = run(["completion"], tmp_path, capsys)

        assert code == 2
        assert "bash|zsh|fish" in err

    def test_printing_still_works_and_writes_nothing(self, tmp_path, capsys):
        from test_cli import run

        code, out, _ = run(["completion", "bash"], tmp_path, capsys)

        assert code == 0
        assert "_runon()" in out
        assert not (tmp_path / "home").exists()


class TestFirstRunInstallsCompletion:
    """`pip install` cannot register a completion, so the first run does.

    A wheel has no post-install hook, and the data files that would place one
    land inside the venv for a venv or pipx install, where no shell reads them.
    """

    @pytest.fixture(autouse=True)
    def _shell(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/bash")
        monkeypatch.delenv("RUNON_NO_COMPLETION", raising=False)

    def _home(self, tmp_path):
        return tmp_path / "home"

    def test_the_first_command_installs_it(self, tmp_path, capsys):
        from test_cli import run

        code, _, err = run(["list", "programs"], tmp_path, capsys)

        assert code == 0
        assert (
            self._home(tmp_path) / ".local/share/bash-completion/completions/runon"
        ).is_file()
        assert "completion installed" in err

    def test_it_happens_once(self, tmp_path, capsys):
        from test_cli import run

        run(["list", "programs"], tmp_path, capsys)
        installed = self._home(tmp_path) / ".local/share/bash-completion/completions/runon"
        installed.unlink()

        _, _, err = run(["list", "programs"], tmp_path, capsys)

        # deciding this again on every command would be a surprise every time
        assert not installed.exists()
        assert "completion installed" not in err

    def test_it_can_be_turned_off(self, tmp_path, monkeypatch, capsys):
        from test_cli import run

        monkeypatch.setenv("RUNON_NO_COMPLETION", "1")
        run(["list", "programs"], tmp_path, capsys)

        assert not (self._home(tmp_path) / ".local").exists()

    def test_an_unknown_shell_installs_nothing_and_says_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        from test_cli import run

        monkeypatch.setenv("SHELL", "/bin/nonesuch")
        _, _, err = run(["list", "programs"], tmp_path, capsys)

        assert "completion" not in err
        assert (config.home() / "completion-installed").is_file()

    def test_running_the_completion_command_does_not_trigger_it(self, tmp_path, capsys):
        from test_cli import run

        # someone managing it themselves should not have it done for them first
        _, _, err = run(["completion", "bash"], tmp_path, capsys)

        assert "completion installed" not in err

    def test_a_failure_to_write_never_fails_the_run(self, tmp_path, monkeypatch, capsys):
        from test_cli import run

        monkeypatch.setattr(
            completion, "install", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
        )

        code, _, _ = run(["list", "programs"], tmp_path, capsys)

        assert code == 0

    def test_the_automatic_path_never_writes_outside_your_home(
        self, tmp_path, monkeypatch, capsys
    ):
        """This wrote into /opt/homebrew before the tests isolated HOME.

        A shared system directory is a fine place to put a completion when
        somebody asked for one, and never a fine place for a side effect of
        `runon list programs`.
        """
        from test_cli import run

        site = tmp_path / "site-functions"
        site.mkdir()
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", (str(site),))
        monkeypatch.setenv("SHELL", "/bin/zsh")

        run(["list", "programs"], tmp_path, capsys)

        assert list(site.iterdir()) == []
        assert (self._home(tmp_path) / ".zsh/completions/_runon").is_file()


class TestShippedCompletions:
    """The wheel ships completion files, and a file can go stale.

    They exist so a system-wide or `--user` install needs no command at all —
    those land under a prefix the shells already read. A copy that drifts from
    the generator would be worse than not shipping one, because it would look
    installed and complete the wrong things.
    """

    FILES = {
        "bash": "share/bash-completion/completions/runon",
        "zsh": "share/zsh/site-functions/_runon",
        "fish": "share/fish/vendor_completions.d/runon.fish",
    }

    def _root(self):
        return Path(__file__).resolve().parents[1]

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_the_shipped_file_matches_what_runon_generates(self, shell):
        path = self._root() / self.FILES[shell]

        assert path.is_file(), f"{path} is missing"
        assert path.read_text(encoding="utf-8") == script(shell), (
            f"{self.FILES[shell]} is stale. Regenerate with:\n"
            "  python -c \"from runon.completion import script; from pathlib import Path; "
            "[Path(p).write_text(script(s)) for s, p in "
            "{'bash':'share/bash-completion/completions/runon',"
            "'zsh':'share/zsh/site-functions/_runon',"
            "'fish':'share/fish/vendor_completions.d/runon.fish'}.items()]\""
        )

    def test_the_wheel_declares_all_three(self):
        import tomllib

        declared = tomllib.loads(
            (self._root() / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["setuptools"]["data-files"]

        shipped = {file for files in declared.values() for file in files}
        assert shipped == set(self.FILES.values())
