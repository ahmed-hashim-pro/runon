"""Finding a host's password without the inventory ever holding one.

The inventory is a file in your workspace that gets committed, so it names
*where* a credential lives — an environment variable or a file — and never the
credential. This resolves those references at the moment ssh needs one.
"""

from __future__ import annotations

import os
import stat
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
