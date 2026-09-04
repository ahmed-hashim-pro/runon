"""Giving ssh a password without putting it anywhere another user can read it.

The obvious tool here is `sshpass`, and it is the wrong one twice over: it is an
extra binary that is not installed by default anywhere, and `sshpass -p` puts the
password in the process table where any user on the box can read it with `ps`.

OpenSSH already has the mechanism — SSH_ASKPASS — so this uses that. The password
is written once to a file that only the current user can read, inside a directory
only the current user can enter, and both are removed when the run ends.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HELPER = """#!/bin/sh
# Written by runon. Prints the password ssh asked for, then nothing else.
cat {path}
"""


@contextmanager
def askpass_env(password: str) -> Iterator[dict[str, str]]:
    """Yields environment variables that make ssh read `password` non-interactively.

    SSH_ASKPASS_REQUIRE=force is what makes this work when a terminal is
    attached; DISPLAY is set as well because OpenSSH before 8.4 consults
    SSH_ASKPASS only when it thinks it has no tty and a display exists.
    """
    directory = Path(tempfile.mkdtemp(prefix="runon-askpass-"))
    try:
        os.chmod(directory, stat.S_IRWXU)  # 0700 — nobody else may even enter it

        secret = directory / "secret"
        secret.write_text(password, encoding="utf-8")
        os.chmod(secret, stat.S_IRUSR | stat.S_IWUSR)  # 0600

        helper = directory / "askpass"
        helper.write_text(HELPER.format(path=_quote(secret)), encoding="utf-8")
        os.chmod(helper, stat.S_IRWXU)  # 0700

        yield {
            "SSH_ASKPASS": str(helper),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
        }
    finally:
        # The password must not outlive the command that needed it, including
        # when that command raised.
        shutil.rmtree(directory, ignore_errors=True)


def _quote(path: Path) -> str:
    import shlex

    return shlex.quote(str(path))
