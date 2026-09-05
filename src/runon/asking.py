"""Collecting the values a program asks for before it runs.

A program declares its inputs in prompts.toml; this fills them in. The same
code serves a person at a terminal and a cron job that has nobody to ask, which
is the only way a program stays runnable in both.
"""

from __future__ import annotations

import getpass
import os
import sys

from .errors import RunonError
from .program import Prompt

PREFIX = "RUNON_PROMPT_"


class Cancelled(Exception):
    """The person answering stopped answering. Not a failure."""


def collect(
    prompts: list[Prompt], *, interactive: bool | None = None, stream=None
) -> dict[str, str]:
    """Answers for every prompt, as RUNON_PROMPT_* names.

    Precedence is environment, then the terminal, then the declared default.
    The environment comes first even when someone is watching: a value passed
    deliberately should not be re-asked, and that is what makes the same
    command work by hand and from a scheduler.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    answers: dict[str, str] = {}
    for prompt in prompts:
        supplied = os.environ.get(prompt.env_name)
        if supplied is not None:
            answers[prompt.env_name] = supplied
            continue
        if interactive:
            answers[prompt.env_name] = _ask(prompt, stream)
            continue
        if prompt.default:
            answers[prompt.env_name] = prompt.default
            continue
        raise RunonError(
            f"this program asks for {prompt.label!r}, which has no default, "
            "and there is no terminal to ask on.\n"
            f"Set {prompt.env_name} in the environment."
        )
    return answers


def _ask(prompt: Prompt, stream) -> str:
    suffix = f" [{prompt.default}]" if prompt.default and not prompt.secret else ""
    label = f"{prompt.label}{suffix}: "
    try:
        if prompt.secret:
            # Never echoed, and never written anywhere runon controls: it
            # reaches the program as an environment variable and stops there.
            value = getpass.getpass(label)
        elif stream is not None:
            print(label, end="", file=sys.stderr, flush=True)
            line = stream.readline()
            if not line:
                raise Cancelled
            value = line.strip()
        else:
            value = input(label).strip()
    except (EOFError, KeyboardInterrupt):
        raise Cancelled from None
    return value or prompt.default
