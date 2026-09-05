"""The command surface, driven the way a user drives it."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import tty

from runon import cli, runner
from runon.picker import choose
from runon.program import Program
from runon.report import emit
from runon.transport import Result


def run(argv, cwd: Path, capsys) -> tuple[int, str, str]:
    import os

    previous = os.getcwd()
    os.chdir(cwd)
    try:
        code = cli.main(argv)
    except SystemExit as exit_:
        # argparse rejects some inputs itself and exits rather than returning;
        # from a user's point of view that is the same outcome.
        code = exit_.code if isinstance(exit_.code, int) else 2
    finally:
        os.chdir(previous)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestInitAndList:
    def test_init_scaffolds_something_that_runs(self, tmp_path, capsys):
        code, out, _ = run(["init", str(tmp_path)], tmp_path, capsys)

        assert code == 0
        assert (tmp_path / "programs" / "hello-world" / "main.sh").is_file()
        assert (tmp_path / "functions" / "say.sh").is_file()
        assert (tmp_path / "inventory.toml").is_file()

    def test_init_refuses_to_overwrite_without_force(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, err = run(["init", str(tmp_path)], tmp_path, capsys)

        assert code == 2
        assert "already exists" in err

    def test_list_programs_shows_the_description_from_main_sh(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(["list", "programs"], tmp_path, capsys)

        assert code == 0
        assert "hello-world" in out
        assert "greeting" in out

    def test_list_hosts_and_groups(self, tmp_path, inventory_file, capsys):
        where = ["-C", str(inventory_file.parent)]
        code, out, _ = run([*where, "list", "hosts"], tmp_path, capsys)
        assert "web-1" in out and "deploy@web-1.example.com" in out

        _, out, _ = run([*where, "list", "groups"], tmp_path, capsys)
        assert "web" in out


class TestLocal:
    def test_runs_a_program_here(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(
            ["local", "run-program", "--program", "hello-world", "-v"], tmp_path, capsys
        )

        assert code == 0
        assert "ok" in out
        assert "hello from" in out

    def test_a_failing_program_exits_non_zero_and_shows_why(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        broken = tmp_path / "programs" / "broken"
        broken.mkdir()
        (broken / "main.sh").write_text("#!/bin/sh\n# Always fails.\necho nope >&2\nexit 4\n")

        code, out, _ = run(["local", "run-program", "--program", "broken"], tmp_path, capsys)

        assert code == 1
        assert "FAILED (4)" in out
        # the reason must be visible without re-running under a flag
        assert "nope" in out

    def test_arguments_reach_the_program(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        echoer = tmp_path / "programs" / "echoer"
        echoer.mkdir()
        (echoer / "main.sh").write_text('#!/bin/sh\n# Echoes.\necho "got:$1"\n')

        _, out, _ = run(
            ["local", "run-program", "--program", "echoer", "-v", "hello"], tmp_path, capsys
        )
        assert "got:hello" in out


class TestErrors:
    def test_unknown_program_lists_what_exists(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, err = run(["local", "run-program", "--program", "nope"], tmp_path, capsys)

        assert code == 2
        assert "hello-world" in err

    def test_unknown_host_is_reported_without_a_traceback(self, tmp_path, inventory_file, capsys):
        code, _, err = run(
            ["host", "--host", "nosuchbox", "run-program", "--program", "x"],
            inventory_file.parent,
            capsys,
        )
        assert code == 2
        assert "Traceback" not in err

    @pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "", "-flag"])
    def test_a_program_name_cannot_escape_the_workspace(self, tmp_path, name, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, _ = run(["local", "run-program", "--program", name], tmp_path, capsys)
        assert code == 2


class TestDryRun:
    def test_names_the_hosts_without_touching_them(self, tmp_path, inventory_file, capsys):
        run(["init", str(inventory_file.parent)], inventory_file.parent, capsys)
        code, out, _ = run(
            ["group", "--group", "web", "run-program", "--program", "hello-world", "--dry-run"],
            inventory_file.parent,
            capsys,
        )

        assert code == 0
        assert "web-1" in out and "web-2" in out
        assert "would" in out


class TestReport:
    def test_exit_code_is_non_zero_if_any_host_failed(self):
        stream = io.StringIO()
        code = emit(
            [Result("a", "x", 0), Result("b", "x", 1, "", "boom")], stream=stream
        )
        # nine of ten machines is not a successful rollout
        assert code == 1
        assert "1/2 ok" in stream.getvalue()

    def test_all_ok_is_zero(self):
        assert emit([Result("a", "x", 0)], stream=io.StringIO()) == 0

    def test_success_output_is_hidden_unless_verbose(self):
        quiet, loud = io.StringIO(), io.StringIO()
        emit([Result("a", "x", 0, "chatty")], stream=quiet)
        emit([Result("a", "x", 0, "chatty")], verbose=True, stream=loud)

        assert "chatty" not in quiet.getvalue()
        assert "chatty" in loud.getvalue()


class TestPicker:
    def _programs(self, tmp_path):
        return [Program("alpha", tmp_path / "alpha"), Program("beta", tmp_path / "beta")]

    def test_selects_by_number(self, tmp_path):
        chosen = choose(
            self._programs(tmp_path), stream=tty("2\n"), prompt_stream=io.StringIO()
        )
        assert chosen.name == "beta"

    def test_blank_input_cancels(self, tmp_path):
        assert choose(
            self._programs(tmp_path), stream=tty("\n"), prompt_stream=io.StringIO()
        ) is None

    def test_a_single_option_needs_no_prompt(self, tmp_path):
        only = [Program("only", tmp_path / "only")]
        assert choose(only, stream=io.StringIO(), prompt_stream=io.StringIO()).name == "only"

    def test_rejects_out_of_range_then_accepts(self, tmp_path):
        chosen = choose(
            self._programs(tmp_path), stream=tty("9\n1\n"), prompt_stream=io.StringIO()
        )
        assert chosen.name == "alpha"


class TestFirstRun:
    """What someone sees before they have set anything up."""

    def test_a_brand_new_machine_can_run_something(self, tmp_path, capsys):
        # nothing set up at all: no config, no workspace, no init
        code, out, _ = run(["local", "run-program", "hello-world", "-v"], tmp_path, capsys)

        assert code == 0
        assert "hello" in out

    def test_running_an_unknown_program_names_what_exists(self, tmp_path, capsys):
        code, _, err = run(["local", "run-program", "--program", "anything"], tmp_path, capsys)

        assert code == 2
        assert "no program named 'anything'" in err
        # naming what does exist beats an empty "Available:" line
        assert "hello-world" in err

    def test_listing_on_a_new_machine_shows_the_sample(self, tmp_path, capsys):
        code, out, _ = run(["list", "programs"], tmp_path, capsys)

        assert code == 0
        assert "hello-world" in out

    def test_the_scaffolded_program_is_executable(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        entry = tmp_path / "programs" / "hello-world" / "main.sh"
        assert entry.stat().st_mode & 0o111, "main.sh is not executable"

    def test_init_then_run_works_with_no_further_setup(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(
            ["local", "run-program", "--program", "hello-world", "-v"], tmp_path, capsys
        )
        # the first thing a new user does must work with no config and no hosts
        assert code == 0
        assert "hello from" in out


class TestPasswordPrompt:
    """--ask-password, at the point where it decides whether to ask."""

    def _args(self, **kw):
        import argparse

        return argparse.Namespace(**{"ask_password": True, "dry_run": False, **kw})

    def test_not_asked_for_unless_requested(self):
        assert cli._password_for(self._args(ask_password=False), [object()]) is None

    def test_asked_once_for_a_single_host(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli.getpass, "getpass", lambda *_: (calls.append(1), "pw")[1])

        assert cli._password_for(self._args(), [object()]) == "pw"
        assert len(calls) == 1

    def test_asked_once_for_a_whole_group(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(cli.getpass, "getpass", lambda *_: (calls.append(1), "pw")[1])

        cli._password_for(self._args(), [object()] * 20)

        # twenty prompts would be its own argument against the feature
        assert len(calls) == 1
        warning = capsys.readouterr().err
        assert "all 20 hosts" in warning
        assert "ssh-copy-id" in warning

    def test_a_dry_run_never_asks(self, monkeypatch):
        monkeypatch.setattr(
            cli.getpass, "getpass", lambda *_: pytest.fail("prompted during a dry run")
        )
        assert cli._password_for(self._args(dry_run=True), [object()]) is None

    def test_an_empty_password_is_refused(self, monkeypatch):
        from runon.errors import RunonError

        monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "")
        with pytest.raises(RunonError):
            cli._password_for(self._args(), [object()])

    def test_the_flag_exists_on_both_remote_scopes(self):
        parser = cli.build_parser()
        for argv in (
            ["host", "--host", "h", "--ask-password", "run-program", "--program", "p"],
            ["group", "--group", "g", "--ask-password", "run-program", "--program", "p"],
        ):
            assert parser.parse_args(argv).ask_password is True

    def test_local_has_no_password_flag(self):
        # nothing to authenticate against; offering it would be a lie
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["local", "run-program", "--ask-password"])


class TestNoTerminal:
    """A menu is not an answer when the run came from cron or CI.

    0.2.1 reached EOF on the first prompt, called it "cancelled" and exited 0
    having done nothing — a rollout that silently skipped every machine.
    """

    def _workspace(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        run(["new-program", "second"], tmp_path, capsys)

    def test_a_missing_program_is_refused_and_names_the_choices(self, tmp_path, capsys):
        self._workspace(tmp_path, capsys)
        code, _, err = run(["local", "run-program", "--dry-run"], tmp_path, capsys)

        assert code == 2
        assert "--program" in err and "hello-world" in err and "second" in err

    @pytest.mark.parametrize(
        ("scope", "flag"), [("host", "--host"), ("group", "--group")]
    )
    def test_a_missing_target_is_refused(self, scope, flag, tmp_path, inventory_file, capsys):
        root = inventory_file.parent
        run(["init", str(root)], root, capsys)
        code, _, err = run(
            [scope, "run-program", "--program", "hello-world", "--dry-run"], root, capsys
        )

        assert code == 2
        assert flag in err

    def test_naming_it_still_works_without_a_terminal(self, tmp_path, capsys):
        self._workspace(tmp_path, capsys)
        code, out, _ = run(
            ["local", "run-program", "--program", "second", "--dry-run"], tmp_path, capsys
        )

        assert code == 0
        assert "second" in out

    def test_one_choice_still_needs_no_flag(self, tmp_path, capsys):
        # deliberate: with a single program there is nothing to ask about
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(["local", "run-program", "--dry-run"], tmp_path, capsys)

        assert code == 0
        assert "hello-world" in out


class TestRemoteVerbs:
    """The verb dispatch, driven through main() against a transport that
    records instead of connecting."""

    @pytest.fixture
    def fake(self, monkeypatch):
        from runon.transport import FakeTransport

        transport = FakeTransport()
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)
        return transport

    def _root(self, inventory_file, capsys):
        run(["init", str(inventory_file.parent)], inventory_file.parent, capsys)
        return inventory_file.parent

    def test_copy_program_copies_and_does_not_run(self, fake, inventory_file, capsys):
        root = self._root(inventory_file, capsys)
        code, _, _ = run(
            ["host", "--host", "web-1", "copy-program", "--program", "hello-world"], root, capsys
        )

        assert code == 0
        assert any("hello-world" in local for _, local, _ in fake.copies)
        # the only thing it runs is the mkdir that scp needs
        assert [command for _, command in fake.calls] == [
            f"mkdir -p {runner.REMOTE_PROGRAMS}"
        ]

    def test_run_program_runs_and_does_not_copy(self, fake, inventory_file, capsys):
        root = self._root(inventory_file, capsys)
        code, _, _ = run(
            ["host", "--host", "web-1", "run-program", "--program", "hello-world"], root, capsys
        )

        assert code == 0
        assert fake.copies == []
        assert [host for host, _ in fake.calls] == ["web-1"]

    def test_copy_run_does_both_in_that_order(self, fake, inventory_file, capsys):
        root = self._root(inventory_file, capsys)
        code, _, _ = run(
            ["host", "--host", "web-1", "copy-run-program", "--program", "hello-world"],
            root,
            capsys,
        )

        assert code == 0
        assert fake.copies and fake.calls

    def test_a_failed_copy_skips_the_run(self, monkeypatch, inventory_file, capsys):
        from runon.transport import FakeTransport

        class RefusesToCopy(FakeTransport):
            def copy(self, host, local, remote):
                return Result(host.name, f"copy {local}", 1, "", "no space left on device")

        transport = RefusesToCopy()
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)
        root = self._root(inventory_file, capsys)

        code, out, _ = run(
            ["host", "--host", "web-1", "copy-run-program", "--program", "hello-world"],
            root,
            capsys,
        )

        assert code == 1
        # running a program that failed to arrive would run the previous version
        assert [command for _, command in transport.calls] == [
            f"mkdir -p {runner.REMOTE_PROGRAMS}"
        ]
        assert "no space left" in out

    def test_a_group_reaches_every_host(self, fake, inventory_file, capsys):
        root = self._root(inventory_file, capsys)
        code, _, _ = run(
            ["group", "--group", "all", "run-program", "--program", "hello-world"], root, capsys
        )

        assert code == 0
        assert sorted(host for host, _ in fake.calls) == ["db-1", "web-1", "web-2"]

    def test_copy_sends_a_plain_directory(self, fake, tmp_path, inventory_file, capsys):
        root = self._root(inventory_file, capsys)
        payload = root / "payload"
        payload.mkdir()
        code, _, _ = run(
            ["host", "--host", "web-1", "copy",
             "--local-dir", str(payload), "--remote-dir", "/tmp/payload"],
            root,
            capsys,
        )

        assert code == 0
        assert fake.copies == [("web-1", str(payload), "/tmp/payload")]


class TestLayoutsAndTemplates:
    def test_run_layout_runs_the_named_script(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        (tmp_path / "layouts" / "marker.sh").write_text("echo layout-ran\n", encoding="utf-8")

        code, out, _ = run(["local", "run-layout", "--layout", "marker"], tmp_path, capsys)

        assert code == 0
        assert "layout-ran" in out

    def test_an_unknown_layout_is_named(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, err = run(["local", "run-layout", "--layout", "nope"], tmp_path, capsys)

        assert code == 2
        assert "nope" in err

    def test_no_layouts_at_all_says_where_they_go(self, tmp_path, capsys):
        code, _, err = run(["local", "run-layout", "--layout", "x"], tmp_path, capsys)

        assert code == 2
        assert "layouts" in err

    def test_new_program_creates_something_that_runs(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, _ = run(["new-program", "fresh"], tmp_path, capsys)
        assert code == 0

        code, out, _ = run(
            ["local", "run-program", "--program", "fresh", "--verbose"], tmp_path, capsys
        )
        assert code == 0
        assert "fresh" in out


class TestWatchWiring:
    """--watch, from the command line down to the call that opens panes."""

    @pytest.fixture
    def opened(self, monkeypatch):
        from runon.transport import FakeTransport

        monkeypatch.setattr(cli, "SSHTransport", lambda **_: FakeTransport())
        seen = {}
        monkeypatch.setattr(
            cli.watch,
            "open_panes",
            lambda hosts, commands, **kw: seen.update(hosts=hosts, commands=commands, **kw)
            or "session-1",
        )
        return seen

    def test_one_pane_per_host(self, opened, inventory_file, capsys):
        root = inventory_file.parent
        run(["init", str(root)], root, capsys)
        code, out, _ = run(
            ["group", "--group", "all", "--watch", "run-program", "--program", "hello-world"],
            root,
            capsys,
        )

        assert code == 0
        assert len(opened["commands"]) == 3
        assert "tmux attach -t session-1" in out

    def test_a_failed_copy_opens_nothing(self, monkeypatch, inventory_file, capsys):
        from runon.transport import FakeTransport

        class RefusesToCopy(FakeTransport):
            def copy(self, host, local, remote):
                return Result(host.name, "copy", 1, "", "denied")

        monkeypatch.setattr(cli, "SSHTransport", lambda **_: RefusesToCopy())
        monkeypatch.setattr(
            cli.watch, "open_panes", lambda *a, **k: pytest.fail("opened panes anyway")
        )
        root = inventory_file.parent
        run(["init", str(root)], root, capsys)

        code, _, err = run(
            ["host", "--host", "web-1", "--watch", "copy-run-program", "--program", "hello-world"],
            root,
            capsys,
        )

        assert code == 1
        assert "nothing to watch" in err


class TestOnePlace:
    """Programs live in one directory, and the config records which one, so a
    command means the same thing from every directory on the machine."""

    def test_init_records_the_workspace(self, tmp_path, capsys):
        from runon import config

        ops = tmp_path / "ops"
        code, out, _ = run(["init", str(ops)], tmp_path, capsys)

        assert code == 0
        assert config.workspace().root == ops.resolve()
        assert str(ops) in out

    def test_programs_are_found_from_an_unrelated_directory(self, tmp_path, capsys):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        run(["init", str(tmp_path / "ops")], tmp_path, capsys)

        code, out, _ = run(["list", "programs"], elsewhere, capsys)

        assert code == 0
        assert "hello-world" in out

    def test_the_inventory_comes_from_the_workspace_too(self, tmp_path, inventory_file, capsys):
        # programs from one directory and hosts from another would be worse
        # than the wandering this replaced
        ops = inventory_file.parent
        run(["init", str(ops)], tmp_path, capsys)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        code, out, _ = run(["list", "hosts"], elsewhere, capsys)

        assert code == 0
        assert "web-1" in out

    def test_an_inventory_beside_you_is_ignored(self, tmp_path, inventory_file, capsys):
        run(["init", str(inventory_file.parent)], tmp_path, capsys)
        stray = tmp_path / "stray"
        stray.mkdir()
        (stray / "inventory.toml").write_text(
            '[hosts.ghost]\naddress = "10.9.9.9"\n', encoding="utf-8"
        )

        code, out, _ = run(["list", "hosts"], stray, capsys)

        assert code == 0
        # both halves: the workspace's hosts are read, the one underfoot is not
        assert "web-1" in out
        assert "ghost" not in out

    def test_dash_C_overrides_for_one_command(self, tmp_path, capsys):
        from runon import config

        run(["init", str(tmp_path / "ops")], tmp_path, capsys)
        other = tmp_path / "other"
        run(["init", str(other)], tmp_path, capsys)
        run(["config", "--workspace", str(tmp_path / "ops")], tmp_path, capsys)

        code, _, _ = run(["-C", str(other), "list", "programs"], tmp_path, capsys)

        assert code == 0
        # the override does not stick
        assert config.workspace().root == (tmp_path / "ops").resolve()

    def test_repointing_says_what_it_was(self, tmp_path, capsys):
        first, second = tmp_path / "first", tmp_path / "second"
        run(["init", str(first)], tmp_path, capsys)
        code, out, _ = run(["init", str(second)], tmp_path, capsys)

        assert code == 0
        assert str(second) in out and str(first) in out and "was" in out

    def test_config_shows_where_things_are(self, tmp_path, capsys):
        from runon import config

        run(["init", str(tmp_path / "ops")], tmp_path, capsys)
        code, out, _ = run(["config"], tmp_path, capsys)

        assert code == 0
        assert str(config.path()) in out
        assert str((tmp_path / "ops").resolve()) in out

    def test_config_refuses_a_directory_that_is_not_a_workspace(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        code, _, err = run(["config", "--workspace", str(empty)], tmp_path, capsys)

        assert code == 2
        assert "no programs" in err

    def test_the_default_workspace_is_made_on_first_use(self, tmp_path, capsys):
        from runon import config

        assert not config.default_root().exists()
        code, out, err = run(["list", "programs"], tmp_path, capsys)

        assert code == 0
        assert (config.default_root() / "programs" / "hello-world").is_dir()
        # the note goes to stderr; stdout is a list of names the shell parses
        assert str(config.default_root()) in err
        assert out.strip().split() [0] == "hello-world"

    def test_bare_init_targets_the_fixed_default(self, tmp_path, capsys):
        from runon import config

        code, out, _ = run(["init"], tmp_path, capsys)

        assert code == 0
        assert str(config.default_root()) in out
        assert not (tmp_path / "programs").exists()

    def test_an_empty_workspace_says_how_to_fill_it(self, tmp_path, capsys):
        ops = tmp_path / "ops"
        run(["init", str(ops)], tmp_path, capsys)
        for child in (ops / "programs").iterdir():
            for f in child.iterdir():
                f.unlink()
            child.rmdir()

        code, _, err = run(["local", "run-program", "--program", "x"], tmp_path, capsys)

        assert code == 2
        assert "new-program" in err and str(ops) in err

    def test_a_hand_edited_config_that_is_broken_says_so(self, tmp_path, capsys):
        from runon import config

        config.path().parent.mkdir(parents=True, exist_ok=True)
        config.path().write_text('workspace = "/tmp\n', encoding="utf-8")

        code, _, err = run(["list", "programs"], tmp_path, capsys)

        assert code == 2
        assert "not valid TOML" in err and str(config.path()) in err

    def test_a_workspace_that_is_not_a_path_says_so(self, tmp_path, capsys):
        from runon import config

        config.path().parent.mkdir(parents=True, exist_ok=True)
        config.path().write_text("workspace = 42\n", encoding="utf-8")

        code, _, err = run(["list", "programs"], tmp_path, capsys)

        assert code == 2
        assert "must be a path" in err

    def test_a_broken_config_can_still_be_repaired(self, tmp_path, capsys):
        """The commands that fix a config must not need it to parse."""
        from runon import config

        ops = tmp_path / "ops"
        run(["init", str(ops)], tmp_path, capsys)
        config.path().write_text('workspace = "/tmp\n', encoding="utf-8")

        assert run(["list", "programs"], tmp_path, capsys)[0] == 2

        assert run(["init", str(ops), "--force"], tmp_path, capsys)[0] == 0
        assert run(["list", "programs"], tmp_path, capsys)[0] == 0

    def test_config_reports_a_broken_file_instead_of_raising(self, tmp_path, capsys):
        from runon import config

        config.path().parent.mkdir(parents=True, exist_ok=True)
        config.path().write_text('workspace = "/tmp\n', encoding="utf-8")

        code, out, _ = run(["config"], tmp_path, capsys)

        assert code == 2
        assert str(config.path()) in out and "unreadable" in out

    @pytest.mark.parametrize("scope", [["doctor"], ["completion", "bash"]])
    def test_a_broken_config_does_not_touch_commands_that_need_no_workspace(
        self, scope, tmp_path, capsys
    ):
        from runon import config

        config.path().parent.mkdir(parents=True, exist_ok=True)
        config.path().write_text('workspace = "/tmp\n', encoding="utf-8")

        assert run(scope, tmp_path, capsys)[0] == 0

    def test_an_empty_listing_puts_nothing_on_stdout(self, tmp_path, capsys):
        """`runon list programs` is parsed by the completion scripts.

        Anything on stdout is offered as a program name, so an explanation
        there becomes 'runon' and 'Run' in your shell's suggestions.
        """
        empty = tmp_path / "empty"
        (empty / "programs").mkdir(parents=True)

        code, out, err = run(["-C", str(empty), "list", "programs"], tmp_path, capsys)

        assert code == 0
        assert out == ""
        assert "new-program" in err


class TestProgramAsPositional:
    """A completed name has to land somewhere that works.

    Before this, `run-program deploy` treated 'deploy' as an argument to a
    program chosen by the picker — so tab-completing a name into that slot
    would have run something else, or nothing.
    """

    def test_the_first_word_is_the_program(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(["local", "run-program", "hello-world", "-v"], tmp_path, capsys)

        assert code == 0
        assert "hello-world" in out

    def test_the_rest_are_its_arguments(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        entry = tmp_path / "programs" / "hello-world" / "main.sh"
        entry.write_text('#!/bin/sh\n# echoes\necho "got: $*"\n', encoding="utf-8")
        entry.chmod(0o755)

        code, out, _ = run(
            ["local", "run-program", "hello-world", "one", "two", "-v"], tmp_path, capsys
        )

        assert code == 0
        assert "got: one two" in out

    def test_the_flag_still_wins_and_keeps_every_positional(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        entry = tmp_path / "programs" / "hello-world" / "main.sh"
        entry.write_text('#!/bin/sh\n# echoes\necho "got: $*"\n', encoding="utf-8")
        entry.chmod(0o755)

        code, out, _ = run(
            ["local", "run-program", "--program", "hello-world", "one", "two", "-v"],
            tmp_path,
            capsys,
        )

        assert code == 0
        assert "got: one two" in out

    def test_an_unknown_first_word_is_an_error_not_a_silent_picker(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, err = run(["local", "run-program", "nope"], tmp_path, capsys)

        assert code == 2
        assert "nope" in err

    def test_it_works_on_the_remote_verbs_too(
        self, monkeypatch, tmp_path, inventory_file, capsys
    ):
        from runon.transport import FakeTransport

        transport = FakeTransport()
        run(["init", str(inventory_file.parent)], tmp_path, capsys)
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)

        code, _, _ = run(
            ["host", "--host", "web-1", "run-program", "hello-world", "80"], tmp_path, capsys
        )

        assert code == 0
        assert any("hello-world" in command and "80" in command for _, command in transport.calls)


class TestHeadless:
    """--headless / --no-tmux: the same command where panes cannot open."""

    @pytest.fixture
    def fake(self, monkeypatch):
        from runon.transport import FakeTransport

        transport = FakeTransport()
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)
        return transport

    def test_it_collects_output_instead_of_opening_panes(
        self, fake, monkeypatch, inventory_file, capsys
    ):
        root = inventory_file.parent
        run(["init", str(root)], root, capsys)
        monkeypatch.setattr(
            cli.watch, "open_panes", lambda *a, **k: pytest.fail("opened panes anyway")
        )

        code, _, err = run(
            ["group", "--group", "web", "--watch", "--no-tmux",
             "run-program", "hello-world"],
            root,
            capsys,
        )

        assert code == 0
        assert sorted(h for h, _ in fake.calls) == ["web-1", "web-2"]
        assert "--no-tmux" in err

    def test_headless_is_the_same_flag(self):
        parser = cli.build_parser()
        argv = ["host", "--host", "h", "--headless", "run-program", "p"]
        assert parser.parse_args(argv).no_tmux is True

    def test_watch_alone_still_opens_panes(self, fake, monkeypatch, inventory_file, capsys):
        root = inventory_file.parent
        run(["init", str(root)], root, capsys)
        opened = {}
        monkeypatch.setattr(
            cli.watch, "open_panes",
            lambda hosts, commands, **kw: opened.update(n=len(commands)) or "s1",
        )

        code, _, _ = run(
            ["group", "--group", "web", "--watch", "run-program", "hello-world"], root, capsys
        )

        assert code == 0
        assert opened["n"] == 2
