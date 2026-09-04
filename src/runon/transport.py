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
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .askpass import askpass_env
from .inventory import Host

#: Long enough that the several connections one command makes reuse one login,
#: short enough that a forgotten terminal is not an open door for the afternoon.
DEFAULT_PERSIST = "60s"


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


class Transport(Protocol):
    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result: ...

    def copy(self, host: Host, local: Path, remote: str) -> Result: ...


def _env_prefix(env: dict[str, str] | None) -> str:
    """Renders env vars as an inline assignment prefix.

    Passing them through ssh's own SendEnv would need matching AcceptEnv on
    every target, which is a server-side change runon has no business
    requiring.
    """
    if not env:
        return ""
    import shlex

    return " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())) + " "


class LocalTransport:
    """Runs on the machine runon was invoked from."""

    name = "local"

    def __init__(self, *, timeout: int = 3600) -> None:
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
        timeout: int = 3600,
        connect_timeout: int = 10,
        password: str | None = None,
        persist: str | None = DEFAULT_PERSIST,
    ) -> None:
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        #: When set, ssh is allowed to authenticate with it. Keys are still
        #: tried first, so a host that has your key never sees the password.
        self.password = password
        #: How long an authenticated connection is kept open for reuse, or None
        #: to open a fresh one every time.
        self.persist = persist

    def _base(self, host: Host, binary: str) -> list[str]:
        argv = [binary, "-o", f"ConnectTimeout={self.connect_timeout}"]
        if self.password is None:
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
            # %C is a hash of the connection parameters, which keeps the socket
            # path under the ~104 character limit a unix socket has.
            argv += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={_socket_dir()}/%C",
                "-o", f"ControlPersist={self.persist}",
            ]
        if host.port:
            # scp spells the port flag differently from ssh, which is a
            # long-standing wart rather than anything clever here.
            argv += ["-P" if binary == "scp" else "-p", str(host.port)]
        return argv

    def ssh_argv(self) -> list[str]:
        """The ssh invocation without a target, for callers that attach a terminal.

        -t forces a tty: without it a program that prompts, or colours its
        output, behaves differently in a pane than it does by hand.
        """
        return [*self._base(Host(name="", address=""), "ssh"), "-t"]

    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result:
        argv = [*self._base(host, "ssh"), host.ssh_target, _env_prefix(env) + command]
        return self._invoke(argv, host, command)

    def _invoke(self, argv: list[str], host: Host, label: str) -> Result:
        """Runs an ssh/scp command, supplying the password if there is one.

        The env built here is for the local ssh process. It is deliberately not
        the same thing as the env passed to `run`, which describes the remote
        command — conflating the two would send SSH_ASKPASS to the target.
        """
        with ExitStack() as stack:
            local_env = None
            if self.password is not None:
                local_env = {**os.environ, **stack.enter_context(askpass_env(self.password))}
            try:
                completed = subprocess.run(
                    argv, capture_output=True, text=True, timeout=self.timeout, env=local_env
                )
            except subprocess.TimeoutExpired:
                return Result(host.name, label, 124, "", f"timed out after {self.timeout}s")
            except FileNotFoundError:
                return Result(host.name, label, 127, "", f"{argv[0]} not found on this machine")
        return Result(host.name, label, completed.returncode, completed.stdout, completed.stderr)

    def copy(self, host: Host, local: Path, remote: str) -> Result:
        argv = [*self._base(host, "scp"), "-r", str(local), f"{host.ssh_target}:{remote}"]
        label = f"copy {local} -> {remote}"
        result = self._invoke(argv, host, label)

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
    copies: list[tuple[str, str, str]] = field(default_factory=list)
    #: Exit code per command substring; the first match wins.
    responses: dict[str, Result] = field(default_factory=dict)
    default_exit: int = 0

    def run(self, host: Host, command: str, *, env: dict[str, str] | None = None) -> Result:
        self.calls.append((host.name, command))
        for needle, result in self.responses.items():
            if needle in command:
                return Result(host.name, command, result.exit_code, result.stdout, result.stderr)
        return Result(host.name, command, self.default_exit)

    def copy(self, host: Host, local: Path, remote: str) -> Result:
        self.copies.append((host.name, str(local), remote))
        return Result(host.name, f"copy {local} -> {remote}", self.default_exit)
