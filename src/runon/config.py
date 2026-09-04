"""Where runon keeps its own settings, as opposed to your programs.

One file in your home directory holding the path to your workspace, so the
command means the same thing from every directory on the machine. Programs live
in one place; this records which place.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from .errors import ConfigError
from .program import Workspace


def home() -> Path:
    """RUNON_HOME, so a test — or a second profile — never touches the real one."""
    return Path(os.environ.get("RUNON_HOME", Path.home() / ".runon"))


def path() -> Path:
    return home() / "config.toml"


def read() -> dict:
    file = path()
    if not file.is_file():
        return {}
    try:
        return tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file} is not valid TOML: {exc}") from exc


def workspace() -> Workspace | None:
    """The configured workspace, or None if `runon init` has not run yet."""
    root = read().get("workspace")
    if not root:
        return None
    if not isinstance(root, str):
        raise ConfigError(f"{path()}: workspace must be a path, not a {type(root).__name__}")
    return Workspace(root=Path(root).expanduser())


def set_workspace(root: Path) -> Path | None:
    """Points the config at `root` and returns the path it used to hold.

    Absolute, because a relative path in a config read from every directory on
    the machine would mean something different in each of them.
    """
    try:
        previous = read().get("workspace")
    except ConfigError:
        # Repointing is how you repair a broken config; needing it to parse
        # first would leave hand-deleting the file as the only way out.
        previous = None
    resolved = root.expanduser().resolve()

    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "# Written by `runon init`. Edit to point at a different workspace.\n"
        # A TOML basic string escapes the way a JSON string does, and a path
        # with a quote or a backslash in it would otherwise write a broken file.
        f"workspace = {json.dumps(str(resolved))}\n",
        encoding="utf-8",
    )
    return Path(previous) if previous else None
