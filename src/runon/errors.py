"""Errors a user can act on.

Every one of these is printed without a traceback, because a stack trace tells
an operator nothing they can use about a typo'd host name.
"""


class RunonError(Exception):
    """Base for anything we expect and can explain."""


class ConfigError(RunonError):
    pass


class UnknownHost(RunonError):
    pass


class UnknownGroup(RunonError):
    pass


class UnknownProgram(RunonError):
    pass


class ProgramInvalid(RunonError):
    pass


class RemoteFailure(RunonError):
    pass
