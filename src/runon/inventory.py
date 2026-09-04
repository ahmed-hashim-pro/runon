"""Who the hosts are, and which groups they belong to.

One TOML file rather than a directory tree of JSON: an operator should be able
to read the whole inventory in one screen and diff it in a review.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError, UnknownGroup, UnknownHost

DEFAULT_FILENAME = "inventory.toml"


@dataclass(frozen=True)
class Host:
    """A machine runon can reach.

    ``address`` is handed to ssh untouched, so anything ssh understands works:
    a bare hostname, user@host, or a Host alias out of ~/.ssh/config.
    """

    name: str
    address: str
    port: int | None = None
    user: str | None = None
    #: Free-form, exported to programs as RUNON_VAR_<KEY>.
    vars: dict[str, str] = field(default_factory=dict)

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.address}" if self.user else self.address


@dataclass(frozen=True)
class Group:
    name: str
    hosts: tuple[str, ...]


@dataclass(frozen=True)
class Inventory:
    hosts: dict[str, Host]
    groups: dict[str, Group]
    source: Path | None = None

    def host(self, name: str) -> Host:
        """Resolves a name, falling back to treating it as a literal address.

        This is what collapses the original tool's separate "named instance" and
        "ad-hoc target" commands into one: `--host web-1` uses the inventory,
        `--host root@10.0.0.4` just works, and neither needs its own verb.
        """
        if name in self.hosts:
            return self.hosts[name]
        if _looks_like_address(name):
            return Host(name=name, address=name)
        raise UnknownHost(
            f"no host named {name!r} in the inventory, and it does not look like an address.\n"
            f"Known hosts: {', '.join(sorted(self.hosts)) or '(none)'}"
        )

    def group(self, name: str) -> list[Host]:
        if name not in self.groups:
            raise UnknownGroup(
                f"no group named {name!r}.\n"
                f"Known groups: {', '.join(sorted(self.groups)) or '(none)'}"
            )
        return [self.host(h) for h in self.groups[name].hosts]


def _looks_like_address(value: str) -> bool:
    """True for things ssh would accept but an inventory lookup missed."""
    return "@" in value or "." in value or value == "localhost"


def load(path: Path | None = None) -> Inventory:
    """Reads an inventory, or returns an empty one when there is no file.

    An absent inventory is not an error: running programs on the local machine
    needs no hosts at all, and requiring a config file to do that would be
    friction for no reason.
    """
    if path is None:
        return Inventory(hosts={}, groups={})
    if not path.is_file():
        raise ConfigError(f"inventory file not found: {path}")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    return _parse(raw, path)


def _parse(raw: dict, source: Path) -> Inventory:
    hosts: dict[str, Host] = {}
    for name, spec in (raw.get("hosts") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{source}: host {name!r} must be a table")
        address = spec.get("address", name)
        port = spec.get("port")
        if port is not None and not isinstance(port, int):
            raise ConfigError(f"{source}: host {name!r} has a non-integer port")
        hosts[name] = Host(
            name=name,
            address=str(address),
            port=port,
            user=spec.get("user"),
            vars={str(k): str(v) for k, v in (spec.get("vars") or {}).items()},
        )

    groups: dict[str, Group] = {}
    for name, spec in (raw.get("groups") or {}).items():
        members = spec.get("hosts") if isinstance(spec, dict) else spec
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise ConfigError(f"{source}: group {name!r} must list host names")
        # Catching this here means a typo in a group fails before runon has
        # half-finished a rollout across the hosts it could resolve.
        unknown = [m for m in members if m not in hosts and not _looks_like_address(m)]
        if unknown:
            raise ConfigError(
                f"{source}: group {name!r} refers to unknown hosts: {', '.join(unknown)}"
            )
        groups[name] = Group(name=name, hosts=tuple(members))

    return Inventory(hosts=hosts, groups=groups, source=source)
