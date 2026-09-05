"""Where runon keeps its own settings, as opposed to your programs.

One file in your home directory holding the path to your workspace, so the
command means the same thing from every directory on the machine. Programs live
in one place; this records which place.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import _tomllib as tomllib
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


def default_root() -> Path:
    """Where programs live when you have not said otherwise.

    A fixed path, so `runon` works the moment it is installed and shell
    completion always has something to offer. The config file can move it; it
    just does not have to be written before anything works.
    """
    return home() / "workspace"


def workspace() -> Workspace:
    """The configured workspace, or the fixed default."""
    root = read().get("workspace")
    if not root:
        return Workspace(root=default_root())
    if not isinstance(root, str):
        raise ConfigError(f"{path()}: workspace must be a path, not a {type(root).__name__}")
    return Workspace(root=Path(root).expanduser())


def is_default(workspace_: Workspace) -> bool:
    return workspace_.root == default_root()


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

    _write({**read_or_empty(), "workspace": str(resolved)})
    return Path(previous) if previous else None


def read_or_empty() -> dict:
    try:
        return read()
    except ConfigError:
        return {}


def _write(data: dict) -> None:
    """Rewrites the config from a dict.

    Safe to serialise wholesale, unlike the inventory: this file is written by
    runon and holds a handful of scalars, so there are no comments to lose.
    """
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by runon. Edit to point at a different workspace."]
    for key, value in sorted(data.items()):
        if isinstance(value, list):
            lines.append(f"{key} = [{', '.join(json.dumps(str(v)) for v in value)}]")
        else:
            # A TOML basic string escapes the way a JSON one does, so a path
            # with a quote or a backslash cannot break the file.
            lines.append(f"{key} = {json.dumps(str(value))}")
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


RECENT_KEY = "recent_programs"
RECENT_MAX = 10


def recent_programs() -> list[str]:
    value = read().get(RECENT_KEY)
    return [str(v) for v in value] if isinstance(value, list) else []


def record_recent(name: str) -> None:
    """Moves `name` to the front of the recents list.

    Best-effort throughout: a config that cannot be written must not stop a
    program from running, because remembering what you ran is a convenience and
    running it is the job.
    """
    try:
        recent = [name] + [r for r in recent_programs() if r != name]
        _write({**read(), RECENT_KEY: recent[:RECENT_MAX]})
    except (OSError, ConfigError):
        pass
