"""fleetsh — run shell programs locally, on one host, or across a group."""

__version__ = "0.1.0"

from .errors import (
    ConfigError,
    FleetshError,
    ProgramInvalid,
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
    "FleetshError",
    "Group",
    "Host",
    "Inventory",
    "LocalTransport",
    "Program",
    "ProgramInvalid",
    "Result",
    "SSHTransport",
    "Transport",
    "UnknownGroup",
    "UnknownHost",
    "UnknownProgram",
    "Workspace",
    "__version__",
]
