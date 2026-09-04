"""Programs and functions: the unit of work, and the reason the tool is small.

A program is a directory with a ``main.sh``. Adding a capability means adding a
directory — the tool itself never changes. That is the whole design: the CLI
knows how to *reach* machines, and shell knows what to *do* on them, and neither
has to learn the other's job.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ProgramInvalid, UnknownProgram

ENTRY_POINT = "main.sh"
PARAMS_FILE = "params.toml"
PROGRAMS_DIR = "programs"
FUNCTIONS_DIR = "functions"
LAYOUTS_DIR = "layouts"

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


@dataclass(frozen=True)
class Program:
    name: str
    path: Path

    @property
    def entry_point(self) -> Path:
        return self.path / ENTRY_POINT

    def params(self) -> dict[str, str]:
        """Values from the program's own params.toml, if it has one.

        Settings that belong to the program rather than to a host: a threshold,
        a branch name, a service to restart. They travel with the program when
        it is copied, so a target always runs it with the values it shipped
        with, and they reach the script as RUNON_PARAM_* so a shell can read
        them without parsing anything.
        """
        path = self.path / PARAMS_FILE
        if not path.is_file():
            return {}
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProgramInvalid(f"{path} is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise ProgramInvalid(f"cannot read {path}: {exc}") from exc
        return {str(k): _as_scalar(v, path, k) for k, v in raw.items()}

    @property
    def description(self) -> str:
        """The first ``# comment`` line of main.sh, if there is one.

        Cheap convention, no metadata file: a program that describes itself in a
        comment is a program whose description cannot drift out of date.
        """
        try:
            for line in self.entry_point.read_text(encoding="utf-8").splitlines()[:10]:
                stripped = line.strip()
                if stripped.startswith("#!"):
                    continue
                if stripped.startswith("#"):
                    text = stripped.lstrip("#").strip()
                    if text:
                        return text
                elif stripped:
                    break
        except OSError:
            pass
        return ""


@dataclass(frozen=True)
class Workspace:
    """The directory holding programs/, functions/ and layouts/."""

    root: Path

    @property
    def programs_path(self) -> Path:
        return self.root / PROGRAMS_DIR

    @property
    def functions_path(self) -> Path:
        return self.root / FUNCTIONS_DIR

    @property
    def layouts_path(self) -> Path:
        return self.root / LAYOUTS_DIR

    def programs(self) -> list[Program]:
        if not self.programs_path.is_dir():
            return []
        found = [
            Program(name=child.name, path=child)
            for child in sorted(self.programs_path.iterdir())
            if child.is_dir() and (child / ENTRY_POINT).is_file()
        ]
        return found

    def program(self, name: str) -> Program:
        for program in self.programs():
            if program.name == name:
                return program
        available = ", ".join(p.name for p in self.programs()) or "(none)"
        raise UnknownProgram(f"no program named {name!r}.\nAvailable: {available}")

    def functions(self) -> list[Path]:
        if not self.functions_path.is_dir():
            return []
        return sorted(p for p in self.functions_path.iterdir() if p.suffix == ".sh")

    def layouts(self) -> list[Program]:
        """Layouts are programs too — a layout is just a script that opens panes."""
        if not self.layouts_path.is_dir():
            return []
        return [
            Program(name=child.stem, path=self.layouts_path)
            for child in sorted(self.layouts_path.iterdir())
            if child.suffix == ".sh"
        ]


def find_workspace(start: Path) -> Workspace | None:
    """Walks up looking for a directory containing programs/."""
    for directory in [start, *start.parents]:
        if (directory / PROGRAMS_DIR).is_dir():
            return Workspace(root=directory)
    return None


def _as_scalar(value: object, path: Path, key: str) -> str:
    """Everything reaching a shell is a string; nesting has no representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    raise ProgramInvalid(
        f"{path}: {key!r} is a {type(value).__name__}. "
        "Parameters become environment variables, so use a string, number or boolean."
    )


def validate_name(name: str) -> str:
    """Program names become path components and shell arguments.

    Rejecting the awkward ones here means nothing downstream has to quote
    defensively, and `--program ../../etc` is refused rather than resolved.
    """
    if not _NAME.match(name):
        raise ProgramInvalid(
            f"{name!r} is not a valid program name. "
            "Use letters, digits, dot, dash or underscore, starting with a letter or digit."
        )
    return name
