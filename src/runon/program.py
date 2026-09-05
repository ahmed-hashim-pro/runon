"""Programs and functions: the unit of work, and the reason the tool is small.

A program is a directory with a ``main.sh``. Adding a capability means adding a
directory — the tool itself never changes. That is the whole design: the CLI
knows how to *reach* machines, and shell knows what to *do* on them, and neither
has to learn the other's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import _tomllib as tomllib
from .errors import ProgramInvalid, UnknownProgram

ENTRY_POINT = "main.sh"
PARAMS_FILE = "params.toml"
META_FILE = "meta.toml"
PROMPTS_FILE = "prompts.toml"
PROGRAMS_DIR = "programs"
FUNCTIONS_DIR = "functions"
LAYOUTS_DIR = "layouts"

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


STATUSES = ("active", "experimental", "deprecated")


@dataclass(frozen=True)
class Meta:
    """What a program says about itself, from an optional meta.toml.

    Optional throughout: a program with no meta.toml behaves exactly as it did
    before there was such a thing, so adding the feature cannot change what an
    existing workspace does.
    """

    title: str = ""
    description: str = ""
    details: str = ""
    category: str = "uncategorized"
    status: str = "active"
    destructive: bool = False
    confirm_message: str = ""
    tags: tuple[str, ...] = ()
    related: tuple[str, ...] = ()


@dataclass(frozen=True)
class Prompt:
    """One value a program asks for before it runs."""

    key: str
    title: str = ""
    default: str = ""
    #: Read with getpass and never echoed. Keeps it off the screen and out of
    #: your shell history — not out of the process table on the target; see
    #: the note in asking._ask.
    secret: bool = False

    @property
    def label(self) -> str:
        return self.title or self.key

    @property
    def env_name(self) -> str:
        """Where an unattended run supplies this instead of typing it."""
        return f"RUNON_PROMPT_{self.key.upper()}"


@dataclass(frozen=True)
class Program:
    name: str
    path: Path

    @property
    def entry_point(self) -> Path:
        return self.path / ENTRY_POINT

    def _table(self, filename: str) -> dict:
        path = self.path / filename
        if not path.is_file():
            return {}
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProgramInvalid(f"{path} is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise ProgramInvalid(f"cannot read {path}: {exc}") from exc

    def meta(self) -> Meta:
        raw = self._table(META_FILE)
        if not raw:
            return Meta()
        status = str(raw.get("status", "active"))
        if status not in STATUSES:
            raise ProgramInvalid(
                f"{self.path / META_FILE}: status {status!r} is not one of "
                f"{', '.join(STATUSES)}"
            )
        return Meta(
            title=str(raw.get("title", "")),
            description=str(raw.get("description", "")),
            details=str(raw.get("details", "")),
            category=str(raw.get("category") or "uncategorized"),
            status=status,
            destructive=bool(raw.get("destructive", False)),
            confirm_message=str(raw.get("confirm_message", "")),
            tags=tuple(str(t) for t in raw.get("tags", ())),
            related=tuple(str(r) for r in raw.get("related", ())),
        )

    def prompts(self) -> list[Prompt]:
        """Values this program asks for, in the order it asks.

        A list of tables rather than one table: order is what makes an
        interview read sensibly, and a TOML table has none.
        """
        raw = self._table(PROMPTS_FILE)
        entries = raw.get("prompt", [])
        if not isinstance(entries, list):
            raise ProgramInvalid(
                f"{self.path / PROMPTS_FILE}: expected [[prompt]] tables, "
                f"got a {type(entries).__name__}"
            )
        prompts = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("key"):
                raise ProgramInvalid(
                    f"{self.path / PROMPTS_FILE}: every [[prompt]] needs a key"
                )
            key = str(entry["key"])
            try:
                validate_prompt_key(key)
            except ProgramInvalid as exc:
                raise ProgramInvalid(f"{self.path / PROMPTS_FILE}: {exc}") from None
            prompts.append(
                Prompt(
                    key=key,
                    title=str(entry.get("title", "")),
                    default=str(entry.get("default", "")),
                    secret=bool(entry.get("secret", False)),
                )
            )
        return prompts

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
        """meta.toml's description, else the first ``# comment`` line of main.sh.

        The comment stays the fallback so a program without meta.toml still
        describes itself, and a description cannot drift out of date by living
        somewhere nobody edits.
        """
        described = self.meta().description
        if described:
            return described
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


def check_runnable(program: Program) -> None:
    """Refuses a main.sh the target's shell cannot execute, and says why.

    Only CRLF line endings, because that is the one that arrives looking like
    something else entirely. A script saved on Windows, or checked out with
    `core.autocrlf=true`, has a shebang ending in a carriage return, so the
    kernel looks for an interpreter called "/usr/bin/env sh\r" and the shell
    reports `./main.sh: not found` — which reads as a missing program, not a
    line ending. Workspaces are meant to be committed and shared, so this
    happens to someone who did nothing wrong.
    """
    try:
        with program.entry_point.open("rb") as handle:
            first = handle.readline()
    except OSError:
        # Unreadable for some other reason: let the run report that itself
        # rather than guessing about it here.
        return
    if first.endswith(b"\r\n"):
        raise ProgramInvalid(
            f"{program.entry_point} has Windows (CRLF) line endings, so the shell "
            f"cannot run it — it reports 'not found' for the interpreter.\n"
            f"Fix it with:  sed -i 's/\\r$//' {program.entry_point}"
        )


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


_PROMPT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_prompt_key(key: str) -> str:
    """A prompt key becomes RUNON_PROMPT_<KEY>, so it has to be a shell name."""
    if not _PROMPT_KEY.match(key or ""):
        raise ProgramInvalid(
            f"{key!r} cannot be an environment variable name; "
            "use letters, digits and underscores, starting with a letter"
        )
    return key


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
