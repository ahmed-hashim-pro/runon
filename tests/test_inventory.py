from __future__ import annotations

import pytest

from runon import inventory
from runon.errors import ConfigError, UnknownGroup, UnknownHost
from runon.inventory import Host, Inventory


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
    assert inventory.load(None).hosts == {}


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


def test_the_workspace_inventory_is_read_from_anywhere(tmp_path, inventory_file):
    # the file no longer has to be near you, only near your programs
    assert inventory.load(inventory_file).hosts.keys() == {"web-1", "web-2", "db-1"}


class TestIPv6:
    """An IPv6 literal is an address by any reading, and was refused.

    Worse, one put in the inventory by hand reached scp unbracketed. scp splits
    host from path on the first colon, so "::1:~/.runon/programs/" became a
    local path and scp quietly did a local copy:
        cp: cannot create directory '::1:~/.runon/programs/'
    ssh needs no brackets, which is why only the scp side adds them.
    """

    def _inv(self):
        return Inventory(hosts={}, groups={})

    @pytest.mark.parametrize("value", ["::1", "2001:db8::1", "fe80::1", "[::1]"])
    def test_it_is_accepted_as_an_ad_hoc_target(self, value):
        assert self._inv().host(value).name == value

    def test_brackets_are_stripped_before_ssh_sees_it(self):
        # ssh cannot resolve a hostname of "[::1]"
        assert self._inv().host("[::1]").address == "::1"

    def test_scp_gets_it_bracketed(self):
        assert Host(name="v6", address="::1").scp_target == "[::1]"

    def test_scp_brackets_only_the_address_not_the_user(self):
        assert Host(name="v6", address="::1", user="ops").scp_target == "ops@[::1]"

    def test_ssh_gets_it_bare(self):
        assert Host(name="v6", address="::1").ssh_target == "::1"
        assert Host(name="v6", address="::1", user="ops").ssh_target == "ops@::1"

    @pytest.mark.parametrize("value", ["web-1.example.com", "10.0.0.4", "host"])
    def test_everything_else_is_untouched(self, value):
        assert Host(name="h", address=value).scp_target == value

    def test_a_bare_word_is_still_refused(self):
        # Typo protection: a mistyped inventory name must not become 10 seconds
        # of ssh failing to resolve it.
        with pytest.raises(UnknownHost):
            self._inv().host("buildbox")

    def test_the_refusal_says_what_to_do_with_an_ssh_alias(self):
        with pytest.raises(UnknownHost) as raised:
            self._inv().host("buildbox")

        assert "runon add-host buildbox --address buildbox" in str(raised.value)
