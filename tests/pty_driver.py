"""Run a command under a pty, send keys, return what it drew.

The picker cannot be tested any other way: it needs a real terminal to put into
raw mode, and its whole job is reacting to keys. Bounded by a deadline and a
SIGKILL so a picker that hangs fails the test instead of the suite.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import time


# The slowest of these takes under a second locally. The deadline is a
# safety net, and a tight one so a picker that hangs fails a test quickly
# instead of stalling every job in the matrix.
def drive(argv: list[str], keys: list[bytes], env: dict | None = None, seconds: float = 5.0):
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child never returns
        os.execve(argv[0], argv, {**os.environ, **(env or {})})

    os.set_blocking(fd, False)
    buf, pending, deadline, last_write = b"", list(keys), time.time() + seconds, 0.0
    try:
        while time.time() < deadline:
            # Nothing is sent until the child has drawn something. Raw mode is
            # entered with TCSAFLUSH, which discards anything typed before the
            # prompt existed, so an eager driver loses its own keystrokes.
            armed = bool(buf) and bool(pending)
            readable, writable, _ = select.select([fd], [fd] if armed else [], [], 0.1)
            if writable and time.time() - last_write > 0.25:
                os.write(fd, pending.pop(0))
                last_write = time.time()
            if readable:
                try:
                    chunk = os.read(fd, 65536)
                except (OSError, BlockingIOError):
                    break
                if not chunk:
                    break
                buf += chunk
            if not pending and os.waitpid(pid, os.WNOHANG)[0]:
                buf += _drain(fd)
                break
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)
    return buf.decode(errors="replace")


def _drain(fd) -> bytes:
    out = b""
    for _ in range(50):
        try:
            chunk = os.read(fd, 65536)
        except (OSError, BlockingIOError):
            break
        if not chunk:
            break
        out += chunk
    return out
