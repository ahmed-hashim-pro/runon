"""Who the hosts are, and which groups they belong to.

One TOML file rather than a directory tree of JSON: an operator should be able
to read the whole inventory in one screen and diff it in a review.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import _tomllib as tomllib
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
    #: Environment variable holding this host's SSH password.
    password_env: str | None = None
    #: File holding this host's SSH password, which must be 0600.
    password_file: str | None = None

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.address}" if self.user else self.address

    @property
    def scp_target(self) -> str:
        """The same target, spelled the way scp needs it.

        scp splits host from path on the first colon, so a bare IPv6 literal
        turns into a hostname of "" and a path of ":1:...", and scp quietly
        falls back to copying locally: `cp: cannot create directory
        '::1:~/.runon/programs/'`. Brackets are how scp is told where the
        address ends. ssh needs no such help, which is why only this side of
        the transport does it.
        """
        if not _is_ipv6(self.address):
            return self.ssh_target
        bracketed = f"[{self.address.strip('[]')}]"
        return f"{self.user}@{bracketed}" if self.user else bracketed


@dataclass(frozen=True)
class Group:
    name: str
    hosts: tuple[str, ...]
    #: Auth for members that do not name their own.
    password_env: str | None = None
    password_file: str | None = None


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
            # Brackets are scp's way of saying where an IPv6 address ends, not
            # part of the address: handed to ssh as typed, "[::1]" is a
            # hostname it cannot resolve. scp_target puts them back.
            address = name.strip("[]") if _is_ipv6(name) else name
            return Host(name=name, address=address)
        raise UnknownHost(
            f"no host named {name!r} in the inventory, and it does not look like an address.\n"
            f"Known hosts: {', '.join(sorted(self.hosts)) or '(none)'}\n"
            f"If {name!r} is a Host alias from your ~/.ssh/config, add it:\n"
            f"  runon add-host {name} --address {name}"
        )

    def group(self, name: str) -> list[Host]:
        if name not in self.groups:
            raise UnknownGroup(
                f"no group named {name!r}.\n"
                f"Known groups: {', '.join(sorted(self.groups)) or '(none)'}"
            )
        group = self.groups[name]
        return [_with_group_auth(self.host(h), group) for h in group.hosts]


def _with_group_auth(host: Host, group: Group) -> Host:
    """Fills in the group's credential for a host that does not name its own.

    A host always wins: a group default is a convenience for the twenty
    machines that share a password, not something that can silently override
    the one machine that does not.
    """
    if host.password_env or host.password_file:
        return host
    if not (group.password_env or group.password_file):
        return host
    return replace(host, password_env=group.password_env, password_file=group.password_file)


def _looks_like_address(value: str) -> bool:
    """True for things ssh would accept but an inventory lookup missed.

    Deliberately not "anything at all": a typo'd inventory name should be
    reported as a typo, not handed to ssh to spend ConnectTimeout failing to
    resolve. A bare word is therefore refused even though ssh might resolve it
    — see the hint in Inventory.host for what to do with an ssh_config alias.

    IPv6 literals are addresses by any reading, and were refused because they
    contain neither an "@" nor a dot.
    """
    return "@" in value or "." in value or value == "localhost" or _is_ipv6(value)


def _is_ipv6(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.strip("[]")).version == 6
    except ValueError:
        return False


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


def _auth_field(spec: dict, key: str, source: Path, kind: str, name: str) -> str | None:
    """Reads password_env / password_file, refusing an inline password.

    `password = "..."` is rejected rather than ignored: the inventory lives in
    your workspace and gets committed, so a secret written here is a secret
    pushed. The error says where to put it instead.
    """
    if "password" in spec:
        raise ConfigError(
            f"{source}: {kind} {name!r} has an inline 'password'.\n"
            "The inventory is committed, so a password here becomes a password in git.\n"
            "Use password_env = \"VAR\" or password_file = \"/path\" (0600) instead."
        )
    value = spec.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{source}: {kind} {name!r} has a non-string {key}")
    return value


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
            password_env=_auth_field(spec, "password_env", source, "host", name),
            password_file=_auth_field(spec, "password_file", source, "host", name),
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
        spec_table = spec if isinstance(spec, dict) else {}
        groups[name] = Group(
            name=name,
            hosts=tuple(members),
            password_env=_auth_field(spec_table, "password_env", source, "group", name),
            password_file=_auth_field(spec_table, "password_file", source, "group", name),
        )

    return Inventory(hosts=hosts, groups=groups, source=source)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_host_name(name: str) -> str:
    """A host name has to be a TOML bare key, or it breaks the file it lands in.

    Checked before writing rather than after: an inventory with a broken table
    header fails every later command, and the cause is one character in a name
    someone typed a week ago.
    """
    if not _BARE_KEY.match(name or ""):
        raise ConfigError(
            f"{name!r} is not a usable host name.\n"
            "Use letters, digits, hyphens and underscores — it becomes a "
            "[hosts.<name>] table in the inventory."
        )
    return name


def append_host(path: Path, host: Host) -> None:
    """Adds a host by appending a table, leaving the rest of the file alone.

    Parsing and rewriting would drop every comment and reorder every table in a
    file this project asks you to read and diff in a review. Appending cannot:
    whatever was above stays byte for byte.
    """
    validate_host_name(host.name)
    existing = load(path) if path.is_file() else Inventory(hosts={}, groups={})
    if host.name in existing.hosts:
        raise ConfigError(
            f"{path} already has a host named {host.name!r}.\n"
            "Edit the file to change it — runon only appends, so it never "
            "rewrites what you wrote."
        )

    # A TOML basic string escapes the way a JSON one does, so a value with a
    # quote or a backslash cannot break the file it is written into.
    lines = [f"\n[hosts.{host.name}]", f"address = {json.dumps(host.address)}"]
    if host.user:
        lines.append(f"user = {json.dumps(host.user)}")
    if host.port:
        lines.append(f"port = {int(host.port)}")
    if host.password_env:
        lines.append(f"password_env = {json.dumps(host.password_env)}")
    if host.password_file:
        lines.append(f"password_file = {json.dumps(host.password_file)}")
    for key, value in sorted(host.vars.items()):
        lines.append(f"vars.{validate_host_name(key)} = {json.dumps(value)}")

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
