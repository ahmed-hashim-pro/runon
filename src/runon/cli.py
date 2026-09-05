"""The command surface.

Three scopes — local, host, group — sharing the same verbs, because where the
work happens and what the work is are separate questions and the CLI should not
tangle them.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from contextlib import suppress
from pathlib import Path

from . import __version__, asking, config, inventory, runner, watch
from . import program as program_mod
from .asking import Cancelled
from .errors import RunonError
from .picker import ADD_NEW, choose, choose_name
from .program import Workspace
from .report import emit
from .transport import DEFAULT_PERSIST, LocalTransport, Result, SSHTransport

REMOTE_VERBS = ("copy", "copy-program", "run-program", "copy-run-program")

#: Exit code for "you were asked, and you said no".
#:
#: runon exits 0 only when it ran what you asked for. Declining a destructive
#: program, or walking away from a menu, means nothing ran — and a script that
#: cannot tell that from success will report a rollout it never did.
CANCELLED = 130


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
        "path", type=Path, nargs="?", help="where to put it (default: ~/.runon/workspace)"
    )
    init.add_argument("--force", action="store_true", help="write into a non-empty directory")
    new = sub.add_parser("new-program", help="create a program from the template")
    new.add_argument("name")

    sub.add_parser("doctor", help="check this machine has what runon needs")

    add_host = sub.add_parser("add-host", help="add a machine to the inventory")
    add_host.add_argument("name", nargs="?", help="what to call it (prompts if omitted)")
    add_host.add_argument("--address", "-a", help="hostname or IP ssh should reach")
    add_host.add_argument("--user", "-u", help="ssh user")
    add_host.add_argument("--port", type=int, help="ssh port, if not 22")
    add_host.add_argument("--group", "-g", help="add it to this group as well")
    # Deliberately no --password: it would land in your shell history, and
    # from there in a file somebody commits.
    add_host.add_argument("--password-env", metavar="VAR", help="env var holding its password")
    add_host.add_argument("--password-file", metavar="PATH", help="0600 file holding it")
    add_host.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin and store it 0600 (for scripts)",
    )

    conf = sub.add_parser("config", help="show or change where your programs live")
    conf.add_argument("--workspace", type=Path, help="point runon at an existing workspace")

    completion = sub.add_parser("completion", help="set up or print shell completion")
    completion.add_argument(
        "shell", nargs="?", choices=["bash", "zsh", "fish"], help="default: your $SHELL"
    )
    completion.add_argument(
        "--install",
        action="store_true",
        help="write it where your shell looks, instead of printing it",
    )

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
        "--no-tmux",
        "--headless",
        dest="no_tmux",
        action="store_true",
        help="with --watch, run over ssh and collect output instead of opening panes",
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
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="agree in advance to a destructive program's confirmation",
    )
    parser.add_argument(
        "args", nargs="*", help="program name, then arguments passed through to main.sh"
    )


def _program_and_args(args) -> tuple[str | None, list[str]]:
    """Splits `run-program deploy 80` into the program and its arguments.

    Without this the first word is a pass-through argument, so tab-completing a
    program name into that slot would silently run something else — or nothing,
    via the picker. `--program` still wins, and then every positional is an
    argument.
    """
    if args.program is not None:
        return args.program, list(args.args)
    if args.args:
        return args.args[0], list(args.args[1:])
    return None, []


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
    except Cancelled:
        print("\ncancelled", file=sys.stderr)
        return CANCELLED
    except RunonError as exc:
        # Expected failures print what went wrong, not where in our code it did.
        print(f"runon: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    if args.scope != "completion":
        # Not for `runon completion`: someone running that is managing it
        # themselves, and doing it for them first would be confusing.
        _install_completion_once()

    # These four run before the workspace is resolved, because a config file
    # that will not parse must not take out the two commands that repair it.
    if args.scope == "init":
        return _init(
            args.path or args.directory or config.default_root(), force=args.force
        )
    if args.scope == "config":
        return _config(args.workspace)
    if args.scope == "add-host":
        return _add_host(_workspace_for(args), args)
    if args.scope == "doctor":
        from .doctor import report, run_checks

        return report(run_checks(), stream=sys.stdout)
    if args.scope == "completion":
        return _completion(args)

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
    """-C for one command, otherwise the one place the config points at."""
    if args.directory:
        return Workspace(root=args.directory)

    workspace = config.workspace()
    if config.is_default(workspace) and not workspace.programs_path.is_dir():
        # The default is filled in on first use rather than waiting for an
        # init: a fixed folder that starts empty gives a new user nothing to
        # run and nothing to complete, which is the friction it exists to fix.
        from .scaffold import write_workspace

        write_workspace(workspace.root)
        print(f"runon: created {workspace.root}", file=sys.stderr)
    return workspace


def _empty_workspace_hint(workspace: Workspace) -> str:
    return (
        f"no programs in {workspace.root}\n"
        f"Run 'runon new-program <name>' to add one, or 'runon config' to see "
        f"where runon is pointed."
    )


def _inventory_in(workspace: Workspace) -> Path | None:
    candidate = workspace.root / "inventory.toml"
    return candidate if candidate.is_file() else None


def _add_host_inline(workspace: Workspace) -> tuple[str, inventory.Inventory]:
    """Adds a host from inside the picker, then re-reads the inventory.

    Re-read rather than patched in memory: the file is the inventory, and a run
    against a host the file does not have would be a confusing thing to debug.
    """
    blank = argparse.Namespace(
        name=None, address=None, user=None, port=None, group=None,
        password_env=None, password_file=None,
    )
    host = _create_host(workspace, blank)
    return host.name, inventory.load(_inventory_in(workspace))


def _add_host(workspace: Workspace, args) -> int:
    _create_host(workspace, args)
    return 0


def _create_host(workspace: Workspace, args) -> inventory.Host:
    """Adds a host, from flags or by asking.

    The two are the same path: anything a flag did not supply is asked for, so
    a scripted call never prompts and a bare call is a short interview.
    """
    path = workspace.root / inventory.DEFAULT_FILENAME
    interactive = sys.stdin.isatty()

    name = args.name or _ask("Name", required=True, interactive=interactive, flag="name")
    inventory.validate_host_name(name)
    address = args.address or _ask(
        "Address (hostname or IP)", required=True, interactive=interactive, flag="--address"
    )
    user = args.user or _ask("SSH user (blank for your own)", interactive=interactive) or None

    password_env, password_file = args.password_env, args.password_file
    if getattr(args, "password_stdin", False):
        from . import secrets

        # Piped rather than typed: a password in argv is in `ps` and in your
        # shell history, which is why there is no --password.
        password_file = str(secrets.write_password_file(name, sys.stdin.read().strip("\n")))
        print(f"  stored password in {password_file}  (0600)")
    if interactive and not (password_env or password_file):
        password_env, password_file = _ask_how_it_authenticates(name)

    host = inventory.Host(
        name=name,
        address=address,
        port=args.port,
        user=user,
        password_env=password_env,
        password_file=password_file,
    )
    if not path.is_file():
        path.write_text("# Hosts runon can reach.\n", encoding="utf-8")
    inventory.append_host(path, host)

    print(f"  added {name}  ({host.ssh_target})  to {path}")
    if args.group:
        print(
            f"\nAdd it to the {args.group!r} group by editing {path}:\n"
            f"  [groups.{args.group}]\n"
            f'  hosts = [..., "{name}"]'
        )
    if not (password_env or password_file):
        print("\nIt will authenticate with your ssh key. Check with: runon doctor")
    return host


def _read(label: str) -> str:
    """input(), with EOF treated as 'stopped', not as a traceback.

    A closed stdin mid-interview is a pipe ending or a Ctrl-D, and neither of
    those deserves a stack trace.
    """
    try:
        return input(label).strip()
    except (EOFError, KeyboardInterrupt):
        raise Cancelled from None


def _ask(label: str, *, required: bool = False, interactive: bool = True, flag: str = "") -> str:
    if not interactive:
        if required:
            raise RunonError(
                f"{label} was not given and there is no terminal to ask on.\n"
                f"Pass {flag}."
            )
        return ""
    while True:
        value = _read(f"{label}: ")
        if value or not required:
            return value
        print("  required")


def _ask_how_it_authenticates(name: str) -> tuple[str | None, str | None]:
    """Asks how a host authenticates, and stores the password if there is one.

    What goes in the inventory is always a reference. The password itself, when
    runon is the one keeping it, goes in a 0600 file under RUNON_HOME — never
    in the workspace, which is committed.
    """
    print("\nHow does it authenticate?")
    print("   1. ssh key (nothing to store)")
    print("   2. password in an environment variable")
    print("   3. password — type it now and runon will store it, 0600")
    print("   4. password in a file I already have")
    while True:
        choice = _read("Select 1-4 [1]: ") or "1"
        if choice == "1":
            return None, None
        if choice == "2":
            var = _read("  Variable name: ")
            if var:
                return var, None
        elif choice == "3":
            path = _store_password_for(name)
            if path:
                return None, str(path)
        elif choice == "4":
            file = _read("  File path: ")
            if file:
                return None, file
        print("  not a choice")


def _store_password_for(name: str) -> Path | None:
    """Reads a password twice and writes it where only this user can read it.

    Twice because a typo here does not fail now — it fails later, as an ssh
    permission error on a machine you were not thinking about.
    """
    from . import secrets

    existing = secrets.secrets_dir() / name
    if existing.exists() and _read(
        f"  {existing} already exists. Replace it? [y/N]: "
    ).lower() not in {"y", "yes"}:
        return None

    for _ in range(3):
        try:
            password = getpass.getpass("  Password: ")
            if password != getpass.getpass("  Again: "):
                print("  they do not match")
                continue
        except (EOFError, KeyboardInterrupt):
            raise Cancelled from None
        if not password:
            print("  an empty password is not a password")
            continue
        path = secrets.write_password_file(name, password)
        print(f"  stored in {path}  (0600)")
        return path
    raise Cancelled


def _install_completion_once() -> None:
    """Set completion up on the first run, since installing cannot.

    A wheel has no way to run anything after `pip install`, and the data files
    that would place a completion land inside the venv for a venv or pipx
    install, where no shell reads them. First run is the earliest moment
    anything of ours executes.

    Once, tracked by a marker, and never on a second attempt after a failure:
    re-deciding this on every command would be a surprise every time somebody
    changed their shell.
    """
    from . import completion

    if os.environ.get("RUNON_NO_COMPLETION") == "1":
        return
    marker = config.home() / "completion-installed"
    # The version too, not just "done". An upgrade that adds a command ships a
    # completion that knows about it, and a marker recording only that
    # something was installed once leaves you completing last year's verbs.
    stamp = f"{__version__}\n"
    if marker.exists() and marker.read_text(encoding="utf-8").endswith(stamp):
        return

    shell = completion.default_shell()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        if shell is None:
            marker.write_text(f"no shell detected\n{stamp}", encoding="utf-8")
            return
        path, remaining = completion.install(shell, user_only=True)
        marker.write_text(f"{path}\n{stamp}", encoding="utf-8")
    except OSError as exc:
        # Nothing here is worth failing a run over. Record the attempt so it
        # is not retried on every command from now on.
        with suppress(OSError):
            marker.write_text(f"failed: {exc}\n{stamp}", encoding="utf-8")
        return

    print(f"runon: {shell} completion installed at {path}", file=sys.stderr)
    if remaining:
        print(f"runon: {remaining.splitlines()[0]}", file=sys.stderr)


def _completion(args) -> int:
    from . import completion

    shell = args.shell or completion.default_shell()
    if shell is None:
        raise RunonError(
            "could not tell which shell you use from $SHELL.\n"
            "Name it: runon completion bash|zsh|fish"
        )

    if not args.install:
        print(completion.script(shell))
        return 0

    path, remaining = completion.install(shell)
    with suppress(OSError):
        (config.home() / "completion-installed").write_text(
            f"{path}\n{__version__}\n", encoding="utf-8"
        )
    print(f"  wrote {path}")
    if remaining:
        print(f"\n{remaining}")
    return 0


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
    default = "  (the default; nothing set)" if config.is_default(current) else ""
    print(f"  workspace  {current.root}{default}")
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
        raise RunonError(_empty_workspace_hint(workspace))
    if name:
        return workspace.program(program_mod.validate_name(name))
    return choose(programs)


def _prepare(program, args) -> dict[str, str]:
    """Announces what a program is, confirms it if it bites, and interviews it.

    Ordered deliberately: you should know what you are about to run before you
    are asked to agree to it, and you should not fill in an interview for a run
    you then decline.
    """
    meta = program.meta()
    if meta.title or meta.description:
        detail = f" — {meta.description}" if meta.description else ""
        # stderr, with the warnings below it: this is context about the run,
        # and stdout is where the run's own result goes.
        print(f"{meta.title or program.name}{detail}", file=sys.stderr)
    if meta.status == "deprecated":
        print("  this program is marked deprecated", file=sys.stderr)
    elif meta.status == "experimental":
        print("  this program is marked experimental", file=sys.stderr)

    if meta.destructive and not args.dry_run:
        _confirm_destructive(program, meta, args)

    return asking.collect(program.prompts())


def _confirm_destructive(program, meta, args) -> None:
    """A program that says it is hard to undo has to be agreed to.

    The warning is printed either way, including when --yes skips the question:
    a log that does not say what it agreed to is a log that cannot tell you why
    the database is gone.

    Refused rather than assumed when nobody is there and nothing said otherwise
    — a scheduled run silently agreeing on your behalf is the thing the flag
    exists to prevent.
    """
    message = meta.confirm_message or "This program makes changes that may be hard to undo."
    print(f"\nDESTRUCTIVE: {message}", file=sys.stderr)

    if getattr(args, "yes", False):
        print("proceeding: --yes", file=sys.stderr)
        return
    if os.environ.get("RUNON_ASSUME_YES") == "1":
        print("proceeding: RUNON_ASSUME_YES=1", file=sys.stderr)
        return
    if not sys.stdin.isatty():
        raise RunonError(
            f"{program.name} is marked destructive and there is no terminal to confirm on.\n"
            "Pass --yes, or set RUNON_ASSUME_YES=1, if you mean it."
        )
    if _read(f"Run {program.name} anyway? [y/N]: ").lower() not in {"y", "yes"}:
        raise Cancelled


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
            return CANCELLED
        script = workspace.layouts_path / f"{chosen.name}.sh"
        result = transport.run(host, f"sh {script}")
        return emit([result], verbose=True)

    name, passthrough = _program_and_args(args)
    program = _resolve_program(workspace, name)
    if program is None:
        return CANCELLED
    if args.dry_run:
        print(f"would run {program.name} on the local machine")
        return 0
    answers = _prepare(program, args)
    config.record_recent(program.name)
    result = runner.run_program(
        transport, host, workspace, program, args=passthrough, remote=False, prompts=answers
    )
    return emit([result], verbose=args.verbose)


def _remote(workspace: Workspace, inv: inventory.Inventory, args) -> int:
    if args.scope == "host" and not args.host and not inv.hosts and not sys.stdin.isatty():
        raise RunonError(
            "no --host given and the inventory has no hosts.\n"
            "Pass --host user@address, or run 'runon add-host'."
        )
    if args.scope == "group" and not args.group and not inv.groups:
        raise RunonError(
            "no --group given and the inventory has no groups.\n"
            "Add a [groups.<name>] table to inventory.toml."
        )

    if args.scope == "host":
        name = args.host or choose_name("host", sorted(inv.hosts), offer_new=True)
        if name is None:
            return CANCELLED
        if name == ADD_NEW:
            name, inv = _add_host_inline(workspace)
        hosts = [inv.host(name)]
        parallel = 1
    else:
        name = args.group or choose_name("group", sorted(inv.groups))
        if name is None:
            return CANCELLED
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

    name, passthrough = _program_and_args(args)
    program = _resolve_program(workspace, name)
    if program is None:
        return CANCELLED
    if args.dry_run:
        _print_plan(hosts, f"{args.verb} {program.name}")
        return 0

    # Once, not per host: a group of twenty is one intent, and being asked the
    # same question twenty times would be its own argument against the feature.
    answers = {} if args.verb == "copy-program" else _prepare(program, args)
    config.record_recent(program.name)

    if getattr(args, "watch", False) and not getattr(args, "no_tmux", False):
        return _watch(transport, hosts, workspace, program, passthrough, answers, args)
    if getattr(args, "no_tmux", False) and getattr(args, "watch", False):
        # Asked for both: the point of --headless is that the same command
        # works where there is no terminal to open panes in, so it wins.
        print("running without panes: --no-tmux", file=sys.stderr)

    def work(host) -> Result | list[Result]:
        if args.verb == "copy-program":
            return runner.copy_program(transport, host, workspace, program)
        if args.verb == "run-program":
            return runner.run_program(
                transport, host, workspace, program, args=passthrough, prompts=answers
            )
        copied = runner.copy_program(transport, host, workspace, program)
        failed = [r for r in copied if not r.ok]
        if failed:
            return failed[0]
        return runner.run_program(
            transport, host, workspace, program, args=passthrough, prompts=answers
        )

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


def _watch(transport, hosts, workspace, program, passthrough, answers, args) -> int:
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

    remote_commands = [
        runner.watch_command(workspace, program, host, args=passthrough, prompts=answers)
        for host in hosts
    ]
    commands = watch.build_commands(hosts, transport.ssh_argv(), remote_commands)
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
            # stderr: `runon list programs` is parsed by the completion
            # scripts, so anything on stdout has to be a name.
            print(_empty_workspace_hint(workspace), file=sys.stderr)
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
    print("      runon local run-program hello-world")
    return 0


def _new_program(workspace: Workspace, name: str, *, describe: bool = True) -> int:
    from .scaffold import write_meta, write_program, write_prompts

    program_mod.validate_name(name)
    path = write_program(workspace.programs_path, name)
    created = [path]

    if describe and sys.stdin.isatty():
        meta, prompts = _interview_about(name)
        if meta:
            created.append(write_meta(path.parent, meta))
        if prompts:
            created.append(write_prompts(path.parent, prompts))

    for made in created:
        print(f"  created {made}")
    return 0


def _interview_about(name: str) -> tuple[dict, list[dict]]:
    """Asks what a program is, so the picker and the run banner can say.

    Every answer is optional — Enter skips — because a description you were
    forced to invent is worse than no description at all.
    """
    print(f"\nDescribe {name}. Enter skips anything.")
    meta = {
        "title": _read("  Title: "),
        "description": _read("  One-line description: "),
        "category": _read("  Category (deploy, checks, maintenance…): "),
    }

    status = _read("  Status [active/experimental/deprecated] (active): ") or "active"
    if status not in program_mod.STATUSES:
        print(f"  {status!r} is not a status; using 'active'", file=sys.stderr)
        status = "active"
    meta["status"] = status

    if _read("  Destructive — hard to undo? [y/N]: ").lower() in {"y", "yes"}:
        meta["destructive"] = True
        meta["confirm_message"] = _read(
            "  What should it warn before running? "
        ) or "This program makes changes that may be hard to undo."

    tags = _read("  Tags (comma-separated): ")
    meta["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    prompts = []
    if _read("\n  Does it ask for anything at run time? [y/N]: ").lower() in {"y", "yes"}:
        print("  Each becomes RUNON_PROMPT_<KEY> for the script. Blank key to finish.")
        while True:
            key = _read("    key: ")
            if not key:
                break
            try:
                program_mod.validate_prompt_key(key)
            except RunonError as exc:
                print(f"    {exc}", file=sys.stderr)
                continue
            prompts.append({
                "key": key,
                "title": _read("    question: "),
                "default": _read("    default: "),
                "secret": _read("    secret — hide while typing? [y/N]: ").lower()
                in {"y", "yes"},
            })

    return ({k: v for k, v in meta.items() if v}, prompts)
