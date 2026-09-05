"""meta.toml, prompts.toml, and the run-time interview they drive."""

from __future__ import annotations

import pytest
from conftest import tty
from test_cli import run

from runon import asking, cli
from runon.errors import ProgramInvalid, RunonError
from runon.program import Program, Prompt


def _program(tmp_path, **files) -> Program:
    directory = tmp_path / "prog"
    directory.mkdir()
    (directory / "main.sh").write_text("#!/bin/sh\n# from the comment\ntrue\n", encoding="utf-8")
    for name, body in files.items():
        (directory / f"{name}.toml").write_text(body, encoding="utf-8")
    return Program("prog", directory)


class TestMeta:
    def test_a_program_without_meta_behaves_as_before(self, tmp_path):
        program = _program(tmp_path)

        assert program.meta().title == ""
        assert program.description == "from the comment"

    def test_meta_describes_it_when_present(self, tmp_path):
        program = _program(tmp_path, meta='title = "Deploy"\ndescription = "Ship it"\n')

        assert program.meta().title == "Deploy"
        assert program.description == "Ship it"

    def test_the_comment_is_still_the_fallback(self, tmp_path):
        program = _program(tmp_path, meta='title = "Deploy"\n')

        # a title is not a description; the comment still answers that
        assert program.description == "from the comment"

    def test_an_unknown_status_is_refused(self, tmp_path):
        program = _program(tmp_path, meta='status = "hopeful"\n')

        with pytest.raises(ProgramInvalid) as excinfo:
            program.meta()
        assert "hopeful" in str(excinfo.value)

    def test_broken_toml_names_the_file(self, tmp_path):
        program = _program(tmp_path, meta='title = "unclosed\n')

        with pytest.raises(ProgramInvalid) as excinfo:
            program.meta()
        assert "meta.toml" in str(excinfo.value)


class TestPrompts:
    def test_they_keep_the_order_they_were_written_in(self, tmp_path):
        program = _program(
            tmp_path,
            prompts='[[prompt]]\nkey = "b"\n\n[[prompt]]\nkey = "a"\n',
        )
        # an interview reads in an order; a TOML table has none
        assert [p.key for p in program.prompts()] == ["b", "a"]

    def test_a_key_that_is_not_a_shell_name_is_refused(self, tmp_path):
        program = _program(tmp_path, prompts='[[prompt]]\nkey = "my-branch"\n')

        with pytest.raises(ProgramInvalid) as excinfo:
            program.prompts()
        assert "environment variable name" in str(excinfo.value)

    def test_a_prompt_without_a_key_is_refused(self, tmp_path):
        program = _program(tmp_path, prompts='[[prompt]]\ntitle = "no key"\n')

        with pytest.raises(ProgramInvalid):
            program.prompts()

    def test_the_env_name_is_derived_from_the_key(self):
        assert Prompt(key="branch").env_name == "RUNON_PROMPT_BRANCH"


class TestCollectingAnswers:
    def test_the_environment_answers_without_asking(self, monkeypatch):
        monkeypatch.setenv("RUNON_PROMPT_BRANCH", "hotfix")
        answers = asking.collect([Prompt(key="branch", default="main")], interactive=True)

        # deliberate even with a terminal: a value passed on purpose is an answer
        assert answers == {"RUNON_PROMPT_BRANCH": "hotfix"}

    def test_a_default_serves_when_nobody_can_be_asked(self, monkeypatch):
        monkeypatch.delenv("RUNON_PROMPT_BRANCH", raising=False)
        answers = asking.collect([Prompt(key="branch", default="main")], interactive=False)

        assert answers == {"RUNON_PROMPT_BRANCH": "main"}

    def test_no_default_and_no_terminal_names_the_variable(self, monkeypatch):
        monkeypatch.delenv("RUNON_PROMPT_TOKEN", raising=False)
        with pytest.raises(RunonError) as excinfo:
            asking.collect([Prompt(key="token", title="Deploy token")], interactive=False)

        assert "RUNON_PROMPT_TOKEN" in str(excinfo.value)
        assert "Deploy token" in str(excinfo.value)

    def test_typing_an_answer(self, monkeypatch):
        monkeypatch.delenv("RUNON_PROMPT_BRANCH", raising=False)
        answers = asking.collect(
            [Prompt(key="branch", default="main")], interactive=True, stream=tty("hotfix\n")
        )
        assert answers == {"RUNON_PROMPT_BRANCH": "hotfix"}

    def test_enter_takes_the_default(self, monkeypatch):
        monkeypatch.delenv("RUNON_PROMPT_BRANCH", raising=False)
        answers = asking.collect(
            [Prompt(key="branch", default="main")], interactive=True, stream=tty("\n")
        )
        assert answers == {"RUNON_PROMPT_BRANCH": "main"}

    def test_a_secret_is_read_without_echoing(self, monkeypatch):
        """getpass, not input: the value must not appear on screen.

        Driven with a stub rather than a pty because getpass uses TCSAFLUSH,
        which correctly discards anything typed before the prompt appeared.
        """
        monkeypatch.delenv("RUNON_PROMPT_TOKEN", raising=False)
        seen = {}
        monkeypatch.setattr(
            asking.getpass, "getpass", lambda label: seen.setdefault("label", label) and "s3cret"
        )

        answers = asking.collect([Prompt(key="token", secret=True)], interactive=True)

        assert answers == {"RUNON_PROMPT_TOKEN": "s3cret"}
        assert seen["label"].startswith("token")

    def test_a_secret_default_is_not_shown_in_the_label(self, monkeypatch):
        monkeypatch.delenv("RUNON_PROMPT_TOKEN", raising=False)
        seen = {}
        monkeypatch.setattr(
            asking.getpass, "getpass", lambda label: seen.setdefault("label", label) and "x"
        )

        asking.collect([Prompt(key="token", default="hunter2", secret=True)], interactive=True)

        assert "hunter2" not in seen["label"]


class TestAnswersReachTheProgram:
    def _workspace(self, tmp_path, capsys, *, meta="", prompts=""):
        run(["init", str(tmp_path)], tmp_path, capsys)
        directory = tmp_path / "programs" / "echoer"
        directory.mkdir()
        entry = directory / "main.sh"
        entry.write_text(
            '#!/bin/sh\n# echoes\necho "branch=${RUNON_PROMPT_BRANCH:-unset}"\n', encoding="utf-8"
        )
        entry.chmod(0o755)
        if meta:
            (directory / "meta.toml").write_text(meta, encoding="utf-8")
        if prompts:
            (directory / "prompts.toml").write_text(prompts, encoding="utf-8")
        return tmp_path

    def test_an_answer_becomes_an_environment_variable(self, tmp_path, monkeypatch, capsys):
        root = self._workspace(
            tmp_path, capsys, prompts='[[prompt]]\nkey = "branch"\ndefault = "main"\n'
        )
        monkeypatch.delenv("RUNON_PROMPT_BRANCH", raising=False)

        code, out, _ = run(["local", "run-program", "echoer", "-v"], root, capsys)

        assert code == 0
        assert "branch=main" in out

    def test_the_environment_wins_over_the_default(self, tmp_path, monkeypatch, capsys):
        root = self._workspace(
            tmp_path, capsys, prompts='[[prompt]]\nkey = "branch"\ndefault = "main"\n'
        )
        monkeypatch.setenv("RUNON_PROMPT_BRANCH", "hotfix")

        code, out, _ = run(["local", "run-program", "echoer", "-v"], root, capsys)

        assert code == 0
        assert "branch=hotfix" in out

    def test_a_prompt_beats_a_param_of_the_same_name(self, tmp_path, monkeypatch, capsys):
        """Run-time input is the most specific thing there is."""
        from runon import runner
        from runon.inventory import Host

        root = self._workspace(
            tmp_path, capsys, prompts='[[prompt]]\nkey = "branch"\ndefault = "main"\n'
        )
        program = __import__("runon.program", fromlist=["Workspace"]).Workspace(
            root=root
        ).program("echoer")
        (program.path / "params.toml").write_text('branch = "from-params"\n', encoding="utf-8")

        env = runner.program_env(
            Host("h", "a"), program, "/fn", {"RUNON_PROMPT_BRANCH": "from-prompt"}
        )

        assert env["RUNON_PARAM_BRANCH"] == "from-params"
        assert env["RUNON_PROMPT_BRANCH"] == "from-prompt"


class TestDestructivePrograms:
    def _workspace(self, tmp_path, capsys, meta):
        run(["init", str(tmp_path)], tmp_path, capsys)
        directory = tmp_path / "programs" / "wiper"
        directory.mkdir()
        entry = directory / "main.sh"
        entry.write_text('#!/bin/sh\n# wipes\necho ran\n', encoding="utf-8")
        entry.chmod(0o755)
        (directory / "meta.toml").write_text(meta, encoding="utf-8")
        return tmp_path

    def test_it_refuses_rather_than_agreeing_on_your_behalf(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")

        code, out, err = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert code == 2
        assert "ran" not in out
        assert "RUNON_ASSUME_YES" in err

    def test_assume_yes_is_how_a_scheduled_run_says_it_means_it(
        self, tmp_path, monkeypatch, capsys
    ):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")
        monkeypatch.setenv("RUNON_ASSUME_YES", "1")

        code, out, _ = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert code == 0
        assert "ran" in out

    def test_the_message_the_program_wrote_is_the_one_shown(self, tmp_path, capsys):
        root = self._workspace(
            tmp_path, capsys, 'destructive = true\nconfirm_message = "Drops the database."\n'
        )

        _, _, err = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert "Drops the database." in err

    def test_yes_agrees_in_advance(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")

        code, out, err = run(["local", "run-program", "wiper", "-v", "--yes"], root, capsys)

        assert code == 0
        assert "ran" in out
        assert "proceeding: --yes" in err

    def test_the_short_form_is_the_same_flag(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")

        code, out, _ = run(["local", "run-program", "wiper", "-v", "-y"], root, capsys)

        assert code == 0
        assert "ran" in out

    def test_the_warning_is_printed_even_when_skipped(self, tmp_path, capsys):
        """A log that does not say what it agreed to cannot tell you why."""
        root = self._workspace(
            tmp_path, capsys, 'destructive = true\nconfirm_message = "Drops the database."\n'
        )

        _, _, err = run(["local", "run-program", "wiper", "-v", "--yes"], root, capsys)

        assert "Drops the database." in err

    def test_yes_works_on_a_remote_verb_too(self, monkeypatch, tmp_path, inventory_file, capsys):
        from runon.transport import FakeTransport

        transport = FakeTransport()
        root = inventory_file.parent
        self._workspace(root, capsys, "destructive = true\n")
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)

        code, _, _ = run(
            ["host", "--host", "web-1", "run-program", "wiper", "--yes"], root, capsys
        )

        assert code == 0
        assert [h for h, _ in transport.calls] == ["web-1"]

    def test_without_it_a_remote_run_still_refuses(
        self, monkeypatch, tmp_path, inventory_file, capsys
    ):
        from runon.transport import FakeTransport

        transport = FakeTransport()
        root = inventory_file.parent
        self._workspace(root, capsys, "destructive = true\n")
        monkeypatch.setattr(cli, "SSHTransport", lambda **_: transport)

        code, _, err = run(["host", "--host", "web-1", "run-program", "wiper"], root, capsys)

        assert code == 2
        assert transport.calls == []
        assert "--yes" in err

    def test_the_refusal_names_both_ways_to_say_yes(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")

        _, _, err = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert "--yes" in err and "RUNON_ASSUME_YES" in err

    def test_a_dry_run_never_asks(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, "destructive = true\n")

        code, _, _ = run(["local", "run-program", "wiper", "--dry-run"], root, capsys)

        assert code == 0

    def test_status_is_reported_without_blocking(self, tmp_path, capsys):
        root = self._workspace(tmp_path, capsys, 'status = "deprecated"\n')

        code, out, err = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert code == 0
        assert "deprecated" in err
        assert "ran" in out


class TestListShowsMeta:
    def test_the_description_comes_from_meta(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        directory = tmp_path / "programs" / "described"
        directory.mkdir()
        (directory / "main.sh").write_text("#!/bin/sh\n# stale comment\n", encoding="utf-8")
        (directory / "meta.toml").write_text('description = "the real one"\n', encoding="utf-8")

        _, out, _ = run(["list", "programs"], tmp_path, capsys)

        assert "the real one" in out
        assert "stale comment" not in out


class TestSayingNoIsNotSuccess:
    """runon exits 0 only when it ran what you asked for.

    Reported from a real machine: declining a destructive program exited 0, so
    `runon ... && notify "deployed"` announced a rollout that never happened.
    """

    def _destructive(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        directory = tmp_path / "programs" / "wiper"
        directory.mkdir()
        entry = directory / "main.sh"
        entry.write_text("#!/bin/sh\n# wipes\necho ran\n", encoding="utf-8")
        entry.chmod(0o755)
        (directory / "meta.toml").write_text("destructive = true\n", encoding="utf-8")
        return tmp_path

    def test_declining_a_destructive_program_is_not_zero(self, tmp_path, monkeypatch, capsys):
        root = self._destructive(tmp_path, capsys)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli, "_read", lambda _: "n")

        code, out, _ = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert code == cli.CANCELLED
        assert code != 0
        assert "ran" not in out

    def test_agreeing_still_is_zero(self, tmp_path, monkeypatch, capsys):
        root = self._destructive(tmp_path, capsys)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli, "_read", lambda _: "y")

        code, out, _ = run(["local", "run-program", "wiper", "-v"], root, capsys)

        assert code == 0
        assert "ran" in out

    def test_walking_away_from_the_menu_is_not_zero_either(
        self, tmp_path, monkeypatch, capsys
    ):
        run(["init", str(tmp_path)], tmp_path, capsys)
        monkeypatch.setattr(cli, "choose", lambda *a, **k: None)

        code, out, _ = run(["local", "run-program"], tmp_path, capsys)

        assert code == cli.CANCELLED
        assert out == ""

    def test_a_cancelled_host_choice_too(self, tmp_path, monkeypatch, inventory_file, capsys):
        run(["init", str(inventory_file.parent)], tmp_path, capsys)
        monkeypatch.setattr(cli, "choose_name", lambda *a, **k: None)

        code, _, _ = run(
            ["-C", str(inventory_file.parent), "host", "run-program", "hello-world"],
            tmp_path,
            capsys,
        )

        assert code == cli.CANCELLED
