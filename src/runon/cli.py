"""The command surface.

Three scopes — local, host, group — sharing the same verbs, because where the
work happens and what the work is are separate questions and the CLI should not
tangle them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, inventory, runner
from . import program as program_mod
from .errors import RunonError
from .picker import choose
from .program import Workspace
from .report import emit
from .transport import LocalTransport, Result, SSHTransport

REMOTE_VERBS = ("copy", "copy-program", "run-program", "copy-run-program")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runon",
        description="Run shell programs on your machine, one server, or a named group.",
    )
    parser.add_argument("--version", action="version", version=f"runon {__version__}")
    parser.add_argument(
        "-C", "--directory", type=Path, help="workspace to use (default: search up)"
    )
    parser.add_argument("--inventory", type=Path, help="inventory file (default: search up)")
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
    host.add_argument("--host", "-H", required=True, help="inventory name, or user@address")
    _add_remote_verbs(host)

    # -- group ---------------------------------------------------------------
    group = sub.add_parser("group", help="run on every machine in a group")
    group.add_argument("--group", "-g", required=True, help="group name from the inventory")
    group.add_argument("--parallel", "-j", type=int, default=1, help="hosts at once (default 1)")
    _add_remote_verbs(group)

    # -- list / init / new ---------------------------------------------------
    listing = sub.add_parser("list", help="show what is available")
    listing.add_argument(
        "what", choices=["programs", "hosts", "groups", "layouts"], nargs="?", default="programs"
    )
    init = sub.add_parser("init", help="scaffold a workspace here")
    init.add_argument("--force", action="store_true", help="write into a non-empty directory")
    new = sub.add_parser("new-program", help="create a program from the template")
    new.add_argument("name")

    return parser


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
    start = args.directory or Path.cwd()
    workspace = program_mod.find_workspace(start) or Workspace(root=start)
    inv = inventory.load(args.inventory, start=start)

    if args.scope == "init":
        return _init(start, force=args.force)
    if args.scope == "new-program":
        return _new_program(workspace, args.name)
    if args.scope == "list":
        return _list(workspace, inv, args.what)
    if args.scope == "local":
        return _local(workspace, args)
    return _remote(workspace, inv, args)


def _resolve_program(workspace: Workspace, name: str | None):
    # An explicitly empty --program is almost always an unset shell variable.
    # Falling through to the picker would make a scripted run do nothing and
    # still report success, so it is refused instead.
    if name is not None and not name.strip():
        raise RunonError("--program was given an empty value")
    if name:
        return workspace.program(program_mod.validate_name(name))
    programs = workspace.programs()
    if not programs:
        raise RunonError(
            f"no programs found under {workspace.programs_path}.\n"
            "Run 'runon init' to scaffold one."
        )
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
    if args.scope == "host":
        hosts = [inv.host(args.host)]
        parallel = 1
    else:
        hosts = inv.group(args.group)
        parallel = max(1, args.parallel)
    if not hosts:
        raise RunonError("no hosts selected")

    transport = SSHTransport()

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


def _print_plan(hosts, action: str) -> None:
    print(f"would {action} on:")
    for host in hosts:
        print(f"  {host.name}  ({host.ssh_target})")


def _list(workspace: Workspace, inv: inventory.Inventory, what: str) -> int:
    if what == "programs":
        items = workspace.programs()
        if not items:
            print(f"no programs under {workspace.programs_path}")
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
    print("\nTry:  runon list programs")
    print("      runon local run-program --program hello-world")
    return 0


def _new_program(workspace: Workspace, name: str) -> int:
    from .scaffold import write_program

    program_mod.validate_name(name)
    path = write_program(workspace.programs_path, name)
    print(f"  created {path}")
    return 0
