"""Errors a user can act on.

Every one of these is printed without a traceback, because a stack trace tells
an operator nothing they can use about a typo'd host name.
"""


class FleetshError(Exception):
    """Base for anything we expect and can explain."""


class ConfigError(FleetshError):
    pass


class UnknownHost(FleetshError):
    pass


class UnknownGroup(FleetshError):
    pass


class UnknownProgram(FleetshError):
    pass


class ProgramInvalid(FleetshError):
    pass


class RemoteFailure(FleetshError):
    pass
