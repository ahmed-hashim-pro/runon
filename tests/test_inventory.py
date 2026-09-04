from __future__ import annotations

import pytest

from fleetsh import inventory
from fleetsh.errors import ConfigError, UnknownGroup, UnknownHost


def test_loads_hosts_and_groups(inventory_file):
    inv = inventory.load(inventory_file)

    assert set(inv.hosts) == {"web-1", "web-2", "db-1"}
    assert inv.host("web-1").ssh_target == "deploy@web-1.example.com"
    assert inv.host("db-1").port == 2222
    assert [h.name for h in inv.group("web")] == ["web-1", "web-2"]


def test_host_vars_are_available(inventory_file):
    assert inventory.load(inventory_file).host("web-1").vars == {"role": "web"}


# The original tool needed separate commands for "a configured instance" and "an
# ad-hoc machine you type the address of". Resolving both here collapses them.
@pytest.mark.parametrize(
    "value",
    ["root@10.0.0.4", "10.0.0.4", "box.example.com", "localhost"],
)
def test_an_unknown_name_that_looks_like_an_address_is_used_directly(inventory_file, value):
    assert inventory.load(inventory_file).host(value).address == value


def test_an_unknown_name_that_is_not_an_address_is_an_error(inventory_file):
    with pytest.raises(UnknownHost) as excinfo:
        inventory.load(inventory_file).host("webb-1")
    # a typo should show what was available, not just say no
    assert "web-1" in str(excinfo.value)


def test_unknown_group_lists_the_real_ones(inventory_file):
    with pytest.raises(UnknownGroup) as excinfo:
        inventory.load(inventory_file).group("prod")
    assert "web" in str(excinfo.value)


def test_a_missing_inventory_is_not_an_error(tmp_path):
    # running programs locally needs no hosts at all
    inv = inventory.load(start=tmp_path)
    assert inv.hosts == {}


def test_a_group_naming_an_unknown_host_fails_at_load(tmp_path):
    path = tmp_path / "inventory.toml"
    path.write_text('[hosts.a]\naddress = "a"\n[groups.g]\nhosts = ["a", "ghost"]\n')
    # better to fail before a rollout starts than halfway through one
    with pytest.raises(ConfigError) as excinfo:
        inventory.load(path)
    assert "ghost" in str(excinfo.value)


def test_broken_toml_says_so(tmp_path):
    path = tmp_path / "inventory.toml"
    path.write_text("[hosts.a\n")
    with pytest.raises(ConfigError) as excinfo:
        inventory.load(path)
    assert "not valid TOML" in str(excinfo.value)


def test_inventory_is_found_by_walking_up(tmp_path, inventory_file):
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert inventory.load(start=nested).hosts.keys() == {"web-1", "web-2", "db-1"}
