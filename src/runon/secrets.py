"""Finding a host's password without the inventory ever holding one.

The inventory is a file in your workspace that gets committed, so it names
*where* a credential lives — an environment variable or a file — and never the
credential. This resolves those references at the moment ssh needs one.
"""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

from .errors import ConfigError
from .inventory import Host


def password_for(host: Host, fallback: str | None = None) -> str | None:
    """The password for `host`, or None to use keys alone.

    `fallback` is what `--ask-password` collected, and it loses to anything the
    host names for itself: a machine that says where its credential lives has
    already answered the question.
    """
    if host.password_env:
        return _from_env(host)
    if host.password_file:
        return _from_file(host)
    return fallback


def _from_env(host: Host) -> str:
    value = os.environ.get(host.password_env or "")
    if not value:
        raise ConfigError(
            f"host {host.name!r} reads its password from ${host.password_env}, "
            "which is unset or empty.\n"
            f"Set it in the environment runon runs in, or use password_file."
        )
    return value


def _from_file(host: Host) -> str:
    path = Path(host.password_file or "").expanduser()
    if not path.is_file():
        raise ConfigError(
            f"host {host.name!r} reads its password from {path}, which does not exist."
        )

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        # Refused rather than warned about: a warning scrolls past, and the
        # window in which the file is readable is the whole point.
        raise ConfigError(
            f"{path} is mode {mode:04o}, which lets other users read it.\n"
            f"Run: chmod 600 {path}"
        )

    # The trailing newline an editor adds is not part of the password, and a
    # password with one would fail authentication in a way nothing explains.
    value = path.read_text(encoding="utf-8").strip("\n")
    if not value:
        raise ConfigError(f"{path} is empty, so there is no password to use.")
    return value


def secrets_dir() -> Path:
    """Where runon keeps passwords it was asked to store.

    Under RUNON_HOME rather than the workspace: the workspace is committed, and
    the entire design here is that a credential never goes near it.
    """
    from .config import home

    directory = home() / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    # 0700 even if it already existed: an earlier umask, or a directory
    # somebody made by hand, must not leave the files inside it reachable.
    directory.chmod(0o700)
    return directory


def write_password_file(name: str, password: str) -> Path:
    """Stores `password` for host `name`, readable only by this user.

    Created with the mode already applied rather than chmod-ed afterwards:
    open-then-chmod leaves a window in which the file exists and anybody can
    read it, and that window is the whole thing being defended against.
    """
    if not password:
        raise ConfigError("an empty password is not a password")

    path = secrets_dir() / name
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(password)
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise
    # An existing file keeps its old mode through O_CREAT, so set it anyway.
    path.chmod(0o600)
    return path
