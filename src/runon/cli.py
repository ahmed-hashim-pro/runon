"""The command surface.

Three scopes — local, host, group — sharing the same verbs, because where the
work happens and what the work is are separate questions and the CLI should not
tangle them.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__, config, inventory, runner, watch
from . import program as program_mod
from .errors import RunonError
from .picker import choose, choose_name
from .program import Workspace
from .report import emit
from .transport import DEFAULT_PERSIST, LocalTransport, Result, SSHTransport

REMOTE_VERBS = ("copy", "copy-program", "run-program", "copy-run-program")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runon",
        description="Run shell programs on your machine, one server, or a named group.",
    )
    parser.add_argument("--version", action="version", version=f"runon {__version__}")
    parser.add_argument(
        "-C", "--directory", type=Path, help="workspace to use, just for this command"
    )
    parser.add_argument(
        "--inventory", type=Path, help="inventory file (default: the workspace's)"
    )
    sub = parser.add_subparsers(dest="scope", required=True)

    # -- local ---------------------------------------------------------------
    local = sub.add_parser("local", help="run on this machine")
    local_sub = local.add_subparsers(dest="verb", required=True)
    run_local = local_sub.add_parser("run-program", help="run a program here")
    _add_program_args(run_local)
    layout = local_sub.add_parser("run-layout", help="open a predefined terminal layout")
    layout.add_argument("--layout", "-l", help="layout name (prompts if omitted)")

    # -- host ----------------------------------------------------------------
    host = sub.add_parser("host", help="run on one machine")
    host.add_argument("--host", "-H", help="inventory name, or user@address (prompts if omitted)")
    _add_auth_args(host)
    _add_remote_verbs(host)

    # -- group ---------------------------------------------------------------
    group = sub.add_parser("group", help="run on every machine in a group")
    group.add_argument("--group", "-g", help="group name from the inventory (prompts if omitted)")
    group.add_argument("--parallel", "-j", type=int, default=1, help="hosts at once (default 1)")
    _add_auth_args(group)
    _add_remote_verbs(group)

    # -- list / init / new ---------------------------------------------------
    listing = sub.add_parser("list", help="show what is available")
    listing.add_argument(
        "what", choices=["programs", "hosts", "groups", "layouts"], nargs="?", default="programs"
    )
    init = sub.add_parser("init", help="scaffold a workspace and remember where it is")
    init.add_argument(
        "path", type=Path, nargs="?", help="where to put it (default: this directory)"
    )
    init.add_argument("--force", action="store_true", help="write into a non-empty directory")
    new = sub.add_parser("new-program", help="create a program from the template")
    new.add_argument("name")

    sub.add_parser("doctor", help="check this machine has what runon needs")

    conf = sub.add_parser("config", help="show or change where your programs live")
    conf.add_argument("--workspace", type=Path, help="point runon at an existing workspace")

    completion = sub.add_parser("completion", help="print a shell completion script")
    completion.add_argument("shell", choices=["bash", "zsh", "fish"])

    return parser


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="prompt for an SSH password (keys are still tried first)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="run in tmux, one pane per host, and attach so you can watch it",
    )
    parser.add_argument(
        "--persist",
        default=DEFAULT_PERSIST,
        metavar="DURATION",
        help=(
            "keep authenticated connections open for reuse "
            f"(default {DEFAULT_PERSIST}; 'no' to disable)"
        ),
    )


def _add_program_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--program", "-p", help="program name (prompts if omitted)")
    parser.add_argument("--verbose", "-v", action="store_true", help="show output from successes")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen")
    parser.add_argument("args", nargs="*", help="arguments passed through to main.sh")


def _add_remote_verbs(parser: argparse.ArgumentParser) -> None:
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in REMOTE_VERBS:
        sub = verbs.add_parser(verb, help=_verb_help(verb))
        if verb == "copy":
            sub.add_argument("--local-dir", type=Path, required=True)
            sub.add_argument("--remote-dir", required=True)
            sub.add_argument("--verbose", "-v", action="store_true")
            sub.add_argument("--dry-run", action="store_true")
        else:
            _add_program_args(sub)


def _verb_help(verb: str) -> str:
    return {
        "copy": "copy a local file or directory to the target(s)",
        "copy-program": "copy a program (and the functions library) to the target(s)",
        "run-program": "run an already-copied program on the target(s)",
        "copy-run-program": "copy and run in one step",
    }[verb]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except RunonError as exc:
        # Expected failures print what went wrong, not where in our code it did.
        print(f"runon: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    # These four run before the workspace is resolved, because a config file
    # that will not parse must not take out the two commands that repair it.
    if args.scope == "init":
        return _init(args.path or args.directory or Path.cwd(), force=args.force)
    if args.scope == "config":
        return _config(args.workspace)
    if args.scope == "doctor":
        from .doctor import report, run_checks

        return report(run_checks(), stream=sys.stdout)
    if args.scope == "completion":
        from .completion import script

        print(script(args.shell))
        return 0

    workspace = _workspace_for(args)
    # From the workspace, never from the cwd: taking programs from one place and
    # hosts from another is worse than the wandering this replaced.
    inv = inventory.load(args.inventory or _inventory_in(workspace))

    if args.scope == "new-program":
        return _new_program(workspace, args.name)
    if args.scope == "list":
        return _list(workspace, inv, args.what)
    if args.scope == "local":
        return _local(workspace, args)
    return _remote(workspace, inv, args)


def _workspace_for(args) -> Workspace:
    """-C for one command, otherwise the one place the config points at.

    Falling back to the cwd matters on a machine with no config yet: `runon
    init` needs somewhere to land, and refusing without a workspace when the
    only way to get one is to init would be a deadlock.
    """
    if args.directory:
        return Workspace(root=args.directory)
    return config.workspace() or Workspace(root=Path.cwd())


def _unconfigured_hint(workspace: Workspace) -> str:
    """What to say when there is nothing to run.

    Two different situations wear the same face: never having run init, and
    pointing the config at a directory that has since moved or been deleted.
    """
    if config.workspace() is None:
        return (
            "runon has no workspace yet.\n"
            "Run 'runon init <dir>' once — it records the path, and every "
            "command then works from anywhere."
        )
    return (
        f"the configured workspace is {workspace.root}, which has no programs.\n"
        f"Check 'runon config', or run 'runon init {workspace.root}' to scaffold it."
    )


def _inventory_in(workspace: Workspace) -> Path | None:
    candidate = workspace.root / "inventory.toml"
    return candidate if candidate.is_file() else None


def _config(new_root: Path | None) -> int:
    if new_root is not None:
        if not (new_root / "programs").is_dir():
            raise RunonError(
                f"{new_root} has no programs/ directory.\n"
                f"Run 'runon init {new_root}' to scaffold one there."
            )
        previous = config.set_workspace(new_root)
        _say_where(new_root, previous)
        return 0

    print(f"  config     {config.path()}")
    try:
        current = config.workspace()
    except RunonError as exc:
        # The command you reach for when something is wrong should not be the
        # command that refuses to run because something is wrong.
        print(f"  workspace  unreadable — {exc}")
        return 2
    print(f"  workspace  {current.root if current else '(not set — run runon init)'}")
    return 0


def _say_where(root: Path, previous: Path | None) -> None:
    resolved = root.expanduser().resolve()
    if previous and previous != resolved:
        # Quietly repointing someone's whole workspace would be a nasty surprise.
        print(f"\nworkspace set to {resolved}  (was {previous})")
    else:
        print(f"\nworkspace set to {resolved}")


def _resolve_program(workspace: Workspace, name: str | None):
    # An explicitly empty --program is almost always an unset shell variable.
    # Falling through to the picker would make a scripted run do nothing and
    # still report success, so it is refused instead.
    if name is not None and not name.strip():
        raise RunonError("--program was given an empty value")

    programs = workspace.programs()
    # Checked before the name is looked up: someone who has not set up a
    # workspace yet needs to be told that, not told their program is missing
    # from an empty list.
    if not programs:
        raise RunonError(_unconfigured_hint(workspace))
    if name:
        return workspace.program(program_mod.validate_name(name))
    return choose(programs)


def _local(workspace: Workspace, args) -> int:
    host = inventory.Host(name="local", address="localhost")
    transport = LocalTransport()

    if args.verb == "run-layout":
        layouts = workspace.layouts()
        if not layouts:
            raise RunonError(f"no layouts found under {workspace.layouts_path}")
        if args.layout:
            chosen = next((lay for lay in layouts if lay.name == args.layout), None)
            if chosen is None:
                raise RunonError(f"no layout named {args.layout!r}")
        else:
            chosen = choose(layouts)
        if chosen is None:
            return 0
        script = workspace.layouts_path / f"{chosen.name}.sh"
        result = transport.run(host, f"sh {script}")
        return emit([result], verbose=True)

    program = _resolve_program(workspace, args.program)
    if program is None:
        return 0
    if args.dry_run:
        print(f"would run {program.name} on the local machine")
        return 0
    result = runner.run_program(transport, host, workspace, program, args=args.args, remote=False)
    return emit([result], verbose=args.verbose)


def _remote(workspace: Workspace, inv: inventory.Inventory, args) -> int:
    if args.scope == "host" and not args.host and not inv.hosts:
        raise RunonError(
            "no --host given and the inventory has no hosts.\n"
            "Pass --host user@address, or add hosts to inventory.toml."
        )
    if args.scope == "group" and not args.group and not inv.groups:
        raise RunonError(
            "no --group given and the inventory has no groups.\n"
            "Add a [groups.<name>] table to inventory.toml."
        )

    if args.scope == "host":
        name = args.host or choose_name("host", sorted(inv.hosts))
        if name is None:
            # Nothing was chosen, which is a decision rather than a failure.
            return 0
        hosts = [inv.host(name)]
        parallel = 1
    else:
        name = args.group or choose_name("group", sorted(inv.groups))
        if name is None:
            return 0
        hosts = inv.group(name)
        parallel = max(1, args.parallel)
    if not hosts:
        raise RunonError("no hosts selected")

    transport = SSHTransport(
        password=_password_for(args, hosts),
        persist=_persist_for(args),
    )

    if args.verb == "copy":
        if args.dry_run:
            _print_plan(hosts, f"copy {args.local_dir} -> {args.remote_dir}")
            return 0
        results = runner.fan_out(
            hosts, lambda h: transport.copy(h, args.local_dir, args.remote_dir), parallel=parallel
        )
        return emit(results, verbose=args.verbose)

    program = _resolve_program(workspace, args.program)
    if program is None:
        return 0
    if args.dry_run:
        _print_plan(hosts, f"{args.verb} {program.name}")
        return 0

    if getattr(args, "watch", False):
        return _watch(transport, hosts, workspace, program, args)

    def work(host) -> Result | list[Result]:
        if args.verb == "copy-program":
            return runner.copy_program(transport, host, workspace, program)
        if args.verb == "run-program":
            return runner.run_program(transport, host, workspace, program, args=args.args)
        copied = runner.copy_program(transport, host, workspace, program)
        failed = [r for r in copied if not r.ok]
        if failed:
            return failed[0]
        return runner.run_program(transport, host, workspace, program, args=args.args)

    results = runner.fan_out(hosts, work, parallel=parallel)
    return emit(results, verbose=args.verbose)


def _persist_for(args) -> str | None:
    """How long to keep a connection open, or None to open a new one each time."""
    value = getattr(args, "persist", DEFAULT_PERSIST)
    if value is None or str(value).lower() in {"no", "none", "off", "0"}:
        return None
    return str(value)


def _password_for(args, hosts) -> str | None:
    """Asks for a password once, if one was asked for.

    Prompted here rather than per host: the same credential is used for every
    machine in a group, and asking twenty times would be its own argument
    against the feature. Keys are still attempted first, so a host that already
    trusts your key never sees it.

    Nothing is stored. The value lives in memory for the length of the run and
    reaches ssh through a helper only this user can read.
    """
    if not getattr(args, "ask_password", False):
        return None
    if args.dry_run:
        return None

    if len(hosts) > 1:
        print(
            f"Using one password for all {len(hosts)} hosts. "
            "If they differ, run them separately — or use ssh-copy-id and stop typing it.",
            file=sys.stderr,
        )
    password = getpass.getpass("SSH password: ")
    if not password:
        raise RunonError("no password entered")
    return password


def _watch(transport, hosts, workspace, program, args) -> int:
    """Runs the program in tmux panes instead of collecting results.

    Copying still happens up front and sequentially, because a pane that starts
    by failing to find the program is not showing you anything useful.
    """
    if args.verb in {"copy-program", "copy-run-program"}:
        copied = runner.fan_out(
            hosts, lambda h: runner.copy_program(transport, h, workspace, program), parallel=1
        )
        failed = [r for r in copied if not r.ok]
        if failed:
            print("copy failed, so there is nothing to watch:", file=sys.stderr)
            return emit(copied)
        if args.verb == "copy-program":
            return emit(copied)

    remote_command = runner.watch_command(workspace, program, args=args.args)
    commands = watch.build_commands(hosts, transport.ssh_argv(), remote_command)
    session = watch.open_panes(hosts, commands, label=program.name)
    print(f"tmux session: {session}  (reattach with: tmux attach -t {session})")
    return 0


def _print_plan(hosts, action: str) -> None:
    print(f"would {action} on:")
    for host in hosts:
        print(f"  {host.name}  ({host.ssh_target})")


def _list(workspace: Workspace, inv: inventory.Inventory, what: str) -> int:
    if what == "programs":
        items = workspace.programs()
        if not items:
            print(_unconfigured_hint(workspace))
            return 0
        width = max(len(p.name) for p in items)
        for p in items:
            print(f"  {p.name:<{width}}  {p.description}".rstrip())
    elif what == "layouts":
        for lay in workspace.layouts():
            print(f"  {lay.name}")
    elif what == "hosts":
        for name, host in sorted(inv.hosts.items()):
            print(f"  {name:<20} {host.ssh_target}")
    else:
        for name, grp in sorted(inv.groups.items()):
            print(f"  {name:<20} {', '.join(grp.hosts)}")
    return 0


def _init(root: Path, *, force: bool) -> int:
    from .scaffold import write_workspace

    created = write_workspace(root, force=force)
    for path in created:
        print(f"  created {path.relative_to(root)}")

    previous = config.set_workspace(root)
    _say_where(root, previous)
    print("\nTry:  runon list programs")
    print("      runon local run-program --program hello-world")
    return 0


def _new_program(workspace: Workspace, name: str) -> int:
    from .scaffold import write_program

    program_mod.validate_name(name)
    path = write_program(workspace.programs_path, name)
    print(f"  created {path}")
    return 0
