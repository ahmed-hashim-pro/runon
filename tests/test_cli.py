"""The command surface, driven the way a user drives it."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from runon import cli
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
        code, out, _ = run(["init"], tmp_path, capsys)

        assert code == 0
        assert (tmp_path / "programs" / "hello-world" / "main.sh").is_file()
        assert (tmp_path / "functions" / "say.sh").is_file()
        assert (tmp_path / "inventory.toml").is_file()

    def test_init_refuses_to_overwrite_without_force(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
        code, _, err = run(["init"], tmp_path, capsys)

        assert code == 2
        assert "already exists" in err

    def test_list_programs_shows_the_description_from_main_sh(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
        code, out, _ = run(["list", "programs"], tmp_path, capsys)

        assert code == 0
        assert "hello-world" in out
        assert "greeting" in out

    def test_list_hosts_and_groups(self, tmp_path, inventory_file, capsys):
        code, out, _ = run(["list", "hosts"], inventory_file.parent, capsys)
        assert "web-1" in out and "deploy@web-1.example.com" in out

        _, out, _ = run(["list", "groups"], inventory_file.parent, capsys)
        assert "web" in out


class TestLocal:
    def test_runs_a_program_here(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
        code, out, _ = run(
            ["local", "run-program", "--program", "hello-world", "-v"], tmp_path, capsys
        )

        assert code == 0
        assert "ok" in out
        assert "hello from" in out

    def test_a_failing_program_exits_non_zero_and_shows_why(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
        broken = tmp_path / "programs" / "broken"
        broken.mkdir()
        (broken / "main.sh").write_text("#!/bin/sh\n# Always fails.\necho nope >&2\nexit 4\n")

        code, out, _ = run(["local", "run-program", "--program", "broken"], tmp_path, capsys)

        assert code == 1
        assert "FAILED (4)" in out
        # the reason must be visible without re-running under a flag
        assert "nope" in out

    def test_arguments_reach_the_program(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
        echoer = tmp_path / "programs" / "echoer"
        echoer.mkdir()
        (echoer / "main.sh").write_text('#!/bin/sh\n# Echoes.\necho "got:$1"\n')

        _, out, _ = run(
            ["local", "run-program", "--program", "echoer", "-v", "hello"], tmp_path, capsys
        )
        assert "got:hello" in out


class TestErrors:
    def test_unknown_program_lists_what_exists(self, tmp_path, capsys):
        run(["init"], tmp_path, capsys)
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
        run(["init"], tmp_path, capsys)
        code, _, _ = run(["local", "run-program", "--program", name], tmp_path, capsys)
        assert code == 2


class TestDryRun:
    def test_names_the_hosts_without_touching_them(self, tmp_path, inventory_file, capsys):
        run(["init"], inventory_file.parent, capsys)
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
            self._programs(tmp_path), stream=io.StringIO("2\n"), prompt_stream=io.StringIO()
        )
        assert chosen.name == "beta"

    def test_blank_input_cancels(self, tmp_path):
        assert choose(
            self._programs(tmp_path), stream=io.StringIO("\n"), prompt_stream=io.StringIO()
        ) is None

    def test_a_single_option_needs_no_prompt(self, tmp_path):
        only = [Program("only", tmp_path / "only")]
        assert choose(only, stream=io.StringIO(), prompt_stream=io.StringIO()).name == "only"

    def test_rejects_out_of_range_then_accepts(self, tmp_path):
        chosen = choose(
            self._programs(tmp_path), stream=io.StringIO("9\n1\n"), prompt_stream=io.StringIO()
        )
        assert chosen.name == "alpha"
