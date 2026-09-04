"""runon — run shell programs locally, on one host, or across a group."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

try:
    # One source of truth: pyproject.toml, read back through the installed
    # metadata. Hardcoding it here meant a release could ship with the package
    # saying one version and `runon --version` saying another, which is exactly
    # what 0.2.0 did.
    __version__ = _installed_version("runon")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

from .errors import (
    ConfigError,
    ProgramInvalid,
    RunonError,
    UnknownGroup,
    UnknownHost,
    UnknownProgram,
)
from .inventory import Group, Host, Inventory
from .program import Program, Workspace
from .transport import FakeTransport, LocalTransport, Result, SSHTransport, Transport

__all__ = [
    "ConfigError",
    "FakeTransport",
    "Group",
    "Host",
    "Inventory",
    "LocalTransport",
    "Program",
    "ProgramInvalid",
    "Result",
    "RunonError",
    "SSHTransport",
    "Transport",
    "UnknownGroup",
    "UnknownHost",
    "UnknownProgram",
    "Workspace",
    "__version__",
]
