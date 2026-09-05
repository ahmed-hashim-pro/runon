"""How a command actually reaches a machine.

Everything that touches another host goes through Transport. That is what makes
the rest of the tool testable without a server: the suite runs against a
recording fake, and the real implementation is a thin shell over the system's
own ssh and scp.

Using the system ssh rather than an embedded SSH library is deliberate. It means
your ~/.ssh/config, your agent, your keys, your ProxyJump and your known_hosts
all work exactly as they already do, and runon never has to grow its own
half-version of any of it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .askpass import askpass_env
from .inventory import Host

#: Long enough that the several connections one command makes reuse one login,
#: short enough that a forgotten terminal is not an open door for the afternoon.
DEFAULT_PERSIST = "60s"

#: How long a single host gets before runon gives up on it. An hour is long
#: enough for the work an operator actually runs this way and short enough that
#: a wedged connection does not hold a rollout open all afternoon. --timeout
#: changes it, and 0 there means no deadline at all.
DEFAULT_TIMEOUT = 3600


@dataclass(frozen=True)
class Result:
    host: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


#: Distinguishes "not passed" from an explicit None, which means "use keys".
_UNSET: Any = object()


class Transport(Protocol):
    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result: ...

    def copy(self, host: Host, local: Path, remote: str) -> Result: ...


class Raw(str):
    """A value that is already shell text, exported without quoting.

    For runon's own paths only. A remote path has to be expanded by the remote
    shell — quoting "$HOME/.runon" makes it a literal dollar sign, and quoting
    "~/.runon" makes it a literal tilde, which is a directory nothing has.

    Everything a user supplies stays quoted, because host vars, parameters and
    prompt answers are values, not shell.
    """


def remote_command(command: str, env: dict[str, str] | None = None) -> str:
    """The single string ssh hands to the target's login shell.

    `env`, then `/bin/sh -c`, because ssh runs this in whatever login shell the
    remote account happens to have and runon has no business caring which.
    It used to send `export A=1; cd dir && ./main.sh`, which assumes a
    Bourne-family shell. Against a tcsh account that is two silent failures at
    once: `export` is not a command there, and `2>` is not how csh spells a
    redirection — so every RUNON_* variable arrived empty, the functions
    library could not be found, and the program still exited 0. runon reported
    `ok` for a run that had none of its inputs. FreeBSD hands root a tcsh by
    default, so this is not a hypothetical account.

    `env` is an ordinary binary, so every shell can invoke it, and the quoted
    inner command is parsed by /bin/sh rather than by the login shell.

    Passing the variables through ssh's own SendEnv would need matching
    AcceptEnv on every target, which is a server-side change runon has no
    business requiring.
    """
    import shlex

    assignments = " ".join(
        f"{k}={v if isinstance(v, Raw) else shlex.quote(v)}"
        for k, v in sorted((env or {}).items())
    )
    # Raw values are deliberately left unquoted: "$HOME/.runon" has to be
    # expanded, and every shell above expands it in this position.
    prefix = f"env {assignments} " if assignments else "env "
    return prefix + "/bin/sh -c " + shlex.quote(command)


class LocalTransport:
    """Runs on the machine runon was invoked from."""

    name = "local"

    def __init__(self, *, timeout: int | None = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result:
        merged = None
        if env:
            import os

            merged = {**os.environ, **env}
        try:
            completed = subprocess.run(
                ["/bin/sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=merged,
            )
        except subprocess.TimeoutExpired:
            return Result(host.name, command, 124, "", f"timed out after {self.timeout}s")
        return Result(host.name, command, completed.returncode, completed.stdout, completed.stderr)

    def copy(self, host: Host, local: Path, remote: str) -> Result:
        destination = Path(remote)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if local.is_dir():
                shutil.copytree(local, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(local, destination)
        except OSError as exc:
            return Result(host.name, f"copy {local} -> {remote}", 1, "", str(exc))
        return Result(host.name, f"copy {local} -> {remote}", 0)


class SSHTransport:
    """Runs over the system ssh and scp binaries."""

    name = "ssh"

    def __init__(
        self,
        *,
        timeout: int | None = DEFAULT_TIMEOUT,
        connect_timeout: int = 10,
        password: str | None = None,
        persist: str | None = DEFAULT_PERSIST,
    ) -> None:
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        #: Collected by --ask-password. A host that names its own credential
        #: in the inventory wins over this; see secrets.password_for.
        self.password = password
        #: How long an authenticated connection is kept open for reuse, or None
        #: to open a fresh one every time.
        self.persist = persist

    def password_for(self, host: Host) -> str | None:
        """This host's password: what it names for itself, else --ask-password."""
        from .secrets import password_for

        return password_for(host, self.password)

    def _base(self, host: Host, binary: str, password: str | None = _UNSET) -> list[str]:
        if password is _UNSET:
            password = self.password_for(host)
        argv = [binary, "-o", f"ConnectTimeout={self.connect_timeout}"]
        if password is None:
            # No password to offer, so refuse to prompt: without this a host
            # missing your key hangs waiting for input, and across a group that
            # is twenty stuck connections and no output.
            argv += ["-o", "BatchMode=yes"]
        else:
            # One attempt only. Three failed prompts per host turns a wrong
            # password into a very slow way to find that out.
            argv += ["-o", "NumberOfPasswordPrompts=1"]

        if self.persist:
            # Connection multiplexing. The first command to a host authenticates
            # and leaves a master connection open; every command after it reuses
            # that socket and authenticates again never. Without this,
            # copy-run-program is two authentications per host, and a second
            # runon command a minute later is another one.
            #
            # None when no short enough directory could be made. Multiplexing is
            # an optimisation, so it is never allowed to be the reason a command
            # fails — see control_path.
            socket = control_path()
            if socket:
                argv += [
                    "-o", "ControlMaster=auto",
                    "-o", f"ControlPath={socket}/%C",
                    "-o", f"ControlPersist={self.persist}",
                ]
        if host.port:
            # scp spells the port flag differently from ssh, which is a
            # long-standing wart rather than anything clever here.
            argv += ["-P" if binary == "scp" else "-p", str(host.port)]
        return argv

    def ssh_argv(self, host: Host | None = None) -> list[str]:
        """The ssh invocation without a target, for callers that attach a terminal.

        -t forces a tty: without it a program that prompts, or colours its
        output, behaves differently in a pane than it does by hand.

        `host` matters. Built without one, this asked `password_for` about a
        blank host, so a machine whose credential is named in the inventory
        looked like a machine with no credential and the pane was handed
        BatchMode=yes — which forbids the very prompt it needed. Every pane
        then died with "Permission denied" while runon reported success.

        A pane's ssh outlives runon, so it cannot use the askpass helper: that
        lives in a temporary directory deleted as soon as the command that made
        it returns. The credential reaches a pane by way of the multiplexed
        master that `prime` opens first, and when there is no master the pane
        has to be allowed to ask.
        """
        host = host if host is not None else Host(name="", address="")
        return [*self._base(host, "ssh"), "-t"]

    def prime(self, host: Host) -> Result:
        """Authenticates once, so a later connection can reuse the master.

        This is what lets a tmux pane reach a password-authenticated host: the
        pane cannot be given a credential, but it can ride a connection that
        already has one. Cheap when multiplexing is off — it is one extra
        login, and it is also the reachability check that stops runon
        reporting a session full of panes that never logged in.
        """
        return self.run(host, "true")

    def multiplexes(self) -> bool:
        """Whether a second connection can reuse the first one's login."""
        return bool(self.persist) and control_path() is not None

    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result:
        password = self.password_for(host)
        argv = [
            *self._base(host, "ssh", password),
            host.ssh_target,
            remote_command(command, env),
        ]
        return self._invoke(argv, host, command, password)

    def _invoke(
        self, argv: list[str], host: Host, label: str, password: str | None = _UNSET
    ) -> Result:
        """Runs an ssh/scp command, supplying the password if there is one.

        The env built here is for the local ssh process. It is deliberately not
        the same thing as the env passed to `run`, which describes the remote
        command — conflating the two would send SSH_ASKPASS to the target.
        """
        if password is _UNSET:
            password = self.password_for(host)
        with ExitStack() as stack:
            local_env = None
            if password is not None:
                local_env = {**os.environ, **stack.enter_context(askpass_env(password))}
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=local_env,
                    # No controlling terminal when a password is in play.
                    # SSH_ASKPASS_REQUIRE=force only exists from OpenSSH 8.4;
                    # before that — Ubuntu 20.04 ships 8.2 — ssh consults the
                    # askpass helper only when it cannot open /dev/tty, so with
                    # a terminal attached it prompted the user instead and the
                    # stored password was never offered.
                    start_new_session=password is not None,
                )
            except subprocess.TimeoutExpired:
                return Result(host.name, label, 124, "", f"timed out after {self.timeout}s")
            except FileNotFoundError:
                return Result(host.name, label, 127, "", f"{argv[0]} not found on this machine")
        return Result(host.name, label, completed.returncode, completed.stdout, completed.stderr)

    def copy(self, host: Host, local: Path, remote: str) -> Result:
        password = self.password_for(host)
        argv = [
            *self._base(host, "scp", password), "-r", str(local), f"{host.scp_target}:{remote}"
        ]
        label = f"copy {local} -> {remote}"
        result = self._invoke(argv, host, label, password)

        stderr = result.stderr
        if not result.ok and _looks_like_missing_sftp(stderr):
            # OpenSSH 9 moved scp onto the SFTP subsystem, so a target with
            # SFTP disabled fails with an error that does not say so.
            stderr += (
                "\n\nhint: this target may have the SFTP subsystem disabled, which scp has "
                "required since OpenSSH 9. On the target run:\n"
                "  echo 'Subsystem sftp internal-sftp' | sudo tee -a /etc/ssh/sshd_config "
                "&& sudo systemctl restart ssh\n"
            )
        return Result(host.name, label, result.exit_code, result.stdout, stderr)


def _socket_dir() -> Path:
    """Where multiplexed connection sockets live.

    0700, because anyone who can reach one of these sockets can use the
    authenticated connection behind it without knowing any credential.
    """
    directory = Path(os.environ.get("RUNON_HOME", Path.home() / ".runon")) / "sockets"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


#: How much room a ControlPath directory has.
#:
#: A unix socket path is capped at 108 bytes on Linux and 104 on macOS. ssh
#: does not bind the final path directly: it binds "<path>.<16 random chars>"
#: and renames, so the real budget is 104 - 1 - 40 (%C is a SHA1 hex digest)
#: - 17 (the suffix) = 46. Taking the smaller of the two caps means a workspace
#: that works on Linux keeps working when the same person tries it on a Mac.
MAX_SOCKET_DIR = 46


def _usable(directory: Path) -> bool:
    """Whether `directory` is ours, private, and safe to put a socket in.

    Checked with lstat rather than trusted: two of the candidates live in
    world-writable places, where another user can get there first and leave a
    symlink pointing at something they can read.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        info = directory.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _socket_candidates() -> list[Path]:
    """Where a control socket could go, best first.

    RUNON_HOME first because that is what the user configured and what
    `runon doctor` talks about. The rest are escape hatches for when it is too
    long or cannot be written — a home directory on a corporate machine is
    routinely something like /home/corp.example.com/firstname.lastname, which
    on its own uses most of the budget.
    """
    candidates = [Path(os.environ.get("RUNON_HOME", Path.home() / ".runon")) / "sockets"]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates.append(Path(runtime) / "runon")
    candidates.append(Path(tempfile.gettempdir()) / f"runon-{os.getuid()}")
    return candidates


def control_path() -> str | None:
    """A directory ssh can actually bind a control socket in, or None.

    None means "run without multiplexing". Connection reuse is a speed-up, and
    a speed-up is never allowed to be the reason a command fails: before this
    chose between candidates, a home directory long enough to push the socket
    past the cap made every single remote command fail with
    "ControlPath too long", and an unwritable RUNON_HOME failed them with a
    bare errno. Both now just cost an extra authentication.
    """
    for candidate in _socket_candidates():
        if len(str(candidate)) <= MAX_SOCKET_DIR and _usable(candidate):
            return str(candidate)
    return None


def _looks_like_missing_sftp(stderr: str) -> bool:
    lowered = stderr.lower()
    return "subsystem request failed" in lowered or "sftp" in lowered


@dataclass
class FakeTransport:
    """Records what would have run, and answers from a script.

    Exported rather than kept in the tests: the hard part of adopting a tool
    like this is proving your programs do the right thing before you point them
    at production, and this is what makes that possible.
    """

    name: str = "fake"
    #: (host, command) pairs, in order.
    calls: list[tuple[str, str]] = field(default_factory=list)
    #: The environment each of those commands would have run with.
    #:
    #: Recorded because half of proving a program does the right thing is the
    #: values it was given, and a fake that drops them can only answer half
    #: the question.
    envs: list[dict[str, str]] = field(default_factory=list)
    copies: list[tuple[str, str, str]] = field(default_factory=list)
    #: Exit code per command substring; the first match wins.
    responses: dict[str, Result] = field(default_factory=dict)
    default_exit: int = 0

    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result:
        self.calls.append((host.name, command))
        self.envs.append(dict(env or {}))
        for needle, result in self.responses.items():
            if needle in command:
                return Result(host.name, command, result.exit_code, result.stdout, result.stderr)
        return Result(host.name, command, self.default_exit)

    def copy(self, host: Host, local: Path, remote: str) -> Result:
        self.copies.append((host.name, str(local), remote))
        return Result(host.name, f"copy {local} -> {remote}", self.default_exit)

    def ssh_argv(self, host: Host | None = None) -> list[str]:
        """Present so a rehearsal can reach --watch, which needs one.

        Not on the Transport protocol: only the ssh transport really has an
        invocation to hand out, and LocalTransport would have to invent one.
        """
        return ["ssh"]

    def prime(self, host: Host) -> Result:
        return self.run(host, "true")

    def multiplexes(self) -> bool:
        return True
