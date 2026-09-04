"""runon — run shell programs locally, on one host, or across a group."""

__version__ = "0.1.0"

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
