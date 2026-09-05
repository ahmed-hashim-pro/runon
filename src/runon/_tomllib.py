"""TOML parsing on every Python runon supports.

`tomllib` entered the standard library in 3.11, and it was the only thing
holding the floor there. Ubuntu 22.04 — still the most widely deployed LTS —
ships 3.10, and `pip install runon` there did not say "your Python is too old",
it said `No matching distribution found for runon`, which reads as the package
not existing at all.

So below 3.11 this falls back to `tomli`, which is the same parser: tomllib was
adopted from it. The dependency is declared for `python_version < "3.11"` only,
so a supported modern Python still installs nothing but runon itself.
"""

from __future__ import annotations

try:  # pragma: no cover - whichever branch runs, the other cannot
    from tomllib import TOMLDecodeError, load, loads
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    from tomli import TOMLDecodeError, load, loads

__all__ = ["TOMLDecodeError", "load", "loads"]
