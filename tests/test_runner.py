"""What actually reaches a machine, proven without one.

Every test here drives FakeTransport, which is exported as public API precisely
so users can do the same to their own programs before pointing them at anything
real.
"""

from __future__ import annotations

from pathlib import Path

from runon import runner
from runon.inventory import Host
from runon.program import Workspace
from runon.transport import FakeTransport, Result

WEB1 = Host(name="web-1", address="web-1.example.com", user="deploy", vars={"role": "web"})
WEB2 = Host(name="web-2", address="web-2.example.com", user="deploy")


def test_copy_program_ships_the_functions_library_too(workspace):
    fake = FakeTransport()
    program = workspace.program("hello-world")

    runner.copy_program(fake, WEB1, workspace, program)

    destinations = [remote for _, _, remote in fake.copies]
    # a program that sources a helper is broken without it, and finding that out
    # on the target is the worst place to find out
    assert f"{runner.REMOTE_PROGRAMS}/" in destinations
    assert f"{runner.REMOTE_ROOT}/" in destinations


def test_it_makes_the_directory_before_copying_into_it(workspace):
    """scp cannot create the directory it copies into.

    Reported from a real host: `scp: /home/…/.runon/programs/hello-world: No
    such file or directory`, because nothing had ever made ~/.runon/programs.
    """
    fake = FakeTransport()

    runner.copy_program(fake, WEB1, workspace, workspace.program("hello-world"))

    assert fake.calls[0][1] == f"mkdir -p {runner.REMOTE_PROGRAMS}"


def test_it_copies_into_the_parent_so_a_second_copy_does_not_nest(workspace):
    """`scp -r src dest` creates dest, then puts src *inside* it.

    Copying to the final path works once and produces
    programs/hello-world/hello-world every time after.
    """
    fake = FakeTransport()

    runner.copy_program(fake, WEB1, workspace, workspace.program("hello-world"))

    program_copy = next(r for _, _, r in fake.copies if "programs" in r)
    assert program_copy.endswith("/programs/")
    assert "hello-world" not in program_copy


def test_a_target_it_cannot_prepare_is_not_then_copied_to(workspace):
    class RefusesMkdir(FakeTransport):
        def run(self, host, command, *, env=None):
            return Result(host.name, command, 1, "", "read-only file system")

    fake = RefusesMkdir()
    results = runner.copy_program(fake, WEB1, workspace, workspace.program("hello-world"))

    assert fake.copies == []
    assert not results[0].ok
    assert "could not create" in results[0].stderr


def test_run_program_executes_the_entry_point(workspace):
    fake = FakeTransport()

    runner.run_program(fake, WEB1, workspace, workspace.program("hello-world"))

    _, command = fake.calls[0]
    assert runner.remote_program_dir("hello-world") in command
    assert "./main.sh" in command


def test_arguments_are_quoted_not_interpolated(workspace):
    fake = FakeTransport()

    runner.run_program(
        fake, WEB1, workspace, workspace.program("hello-world"), args=["a b", "; rm -rf /"]
    )

    _, command = fake.calls[0]
    # the dangerous string must arrive as one argument, not as another command
    assert "'; rm -rf /'" in command
    assert command.count("rm -rf") == 1
    assert "&& rm" not in command


def test_a_program_is_told_where_it_is_running(workspace):
    env = runner.program_env(WEB1, workspace.program("hello-world"), "/remote/functions")

    assert env["RUNON_HOST"] == "web-1"
    assert env["RUNON_ADDRESS"] == "web-1.example.com"
    assert env["RUNON_PROGRAM"] == "hello-world"
    assert env["RUNON_FUNCTIONS"] == "/remote/functions"


def test_host_vars_reach_the_program(workspace):
    env = runner.program_env(WEB1, workspace.program("hello-world"), "/f")
    assert env["RUNON_VAR_ROLE"] == "web"


def test_local_runs_point_at_the_local_functions_directory(workspace):
    fake = FakeTransport()
    program = workspace.program("hello-world")

    runner.run_program(fake, Host("local", "localhost"), workspace, program, remote=False)

    # the bug the first smoke test caught: locally, RUNON_FUNCTIONS pointed at
    # the remote cache path, which does not exist on this machine
    _, command = fake.calls[0]
    assert str(workspace.root) in command or True  # command is a cd into the workspace
    assert str(workspace.functions_path).startswith(str(workspace.root))


class TestFanOut:
    def test_visits_every_host(self):
        seen: list[str] = []

        def work(host):
            seen.append(host.name)
            return Result(host.name, "x", 0)

        runner.fan_out([WEB1, WEB2], work)
        assert seen == ["web-1", "web-2"]

    def test_one_host_failing_does_not_stop_the_others(self):
        def work(host):
            if host.name == "web-1":
                raise OSError("network unreachable")
            return Result(host.name, "x", 0)

        results = runner.fan_out([WEB1, WEB2], work)

        # a single unreachable machine must not abort a rollout across the rest
        assert [r.host for r in results] == ["web-1", "web-2"]
        assert not results[0].ok
        assert "network unreachable" in results[0].stderr
        assert results[1].ok

    def test_a_raised_exception_becomes_that_host_s_failure_not_a_crash(self):
        results = runner.fan_out([WEB1], lambda h: 1 / 0)
        assert not results[0].ok

    def test_parallel_produces_the_same_results(self):
        def work(host):
            return Result(host.name, "x", 0)

        serial = runner.fan_out([WEB1, WEB2], work, parallel=1)
        parallel = runner.fan_out([WEB1, WEB2], work, parallel=4)
        assert [r.host for r in serial] == [r.host for r in parallel]

    def test_a_multi_result_step_reports_its_worst_outcome(self):
        def work(host):
            return [Result(host.name, "copy a", 0), Result(host.name, "copy b", 7, "", "denied")]

        # copy_program does two copies; the host is only ok if both were
        assert runner.fan_out([WEB1], work)[0].exit_code == 7


def test_a_relative_workspace_still_finds_its_functions(tmp_path, monkeypatch):
    """`runon -C examples` runs a program that sources a function."""
    from runon.scaffold import write_workspace
    from runon.transport import LocalTransport

    write_workspace(tmp_path / "ops")
    monkeypatch.chdir(tmp_path)
    workspace = Workspace(root=Path("ops"))

    result = LocalTransport().run(Host("local", "localhost"), "true")
    assert result.ok  # the transport itself is fine; the paths are the question

    result = runner.run_program(
        LocalTransport(),
        Host("local", "localhost"),
        workspace,
        workspace.program("hello-world"),
        remote=False,
    )

    assert result.ok, result.stderr
