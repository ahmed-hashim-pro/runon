"""Printing results in a way an operator can scan."""

from __future__ import annotations

import sys
from collections.abc import Iterable

from .transport import Result


def emit(results: Iterable[Result], *, verbose: bool = False, stream=None) -> int:
    """Prints one line per host and returns the exit code for the whole run.

    Non-zero if any host failed, because a rollout that worked on nine of ten
    machines has not worked.
    """
    stream = stream or sys.stdout
    results = list(results)
    failures = 0

    for result in results:
        status = "ok" if result.ok else f"FAILED ({result.exit_code})"
        print(f"{result.host:<24} {status}", file=stream)
        if result.ok and verbose and result.stdout.strip():
            _indent(result.stdout, stream)
        if not result.ok:
            failures += 1
            # Always show why a failure failed. Needing a flag to see the error
            # means running it twice.
            if result.stderr.strip():
                _indent(result.stderr, stream)
            elif result.stdout.strip():
                _indent(result.stdout, stream)

    if len(results) > 1:
        print(f"\n{len(results) - failures}/{len(results)} ok", file=stream)
    return 1 if failures else 0


def _indent(text: str, stream) -> None:
    for line in text.rstrip().splitlines():
        print(f"    {line}", file=stream)
