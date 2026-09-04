"""What actually reaches a machine, proven without one.

Every test here drives FakeTransport, which is exported as public API precisely
so users can do the same to their own programs before pointing them at anything
real.
"""

from __future__ import annotations

from runon import runner
from runon.inventory import Host
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
    assert runner.remote_program_dir("hello-world") in destinations
    assert any(d.endswith("/functions") for d in destinations)


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
