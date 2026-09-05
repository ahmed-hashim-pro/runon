"""Adding machines, and finding their passwords without storing any."""

from __future__ import annotations

import stat

import pytest
from conftest import tty
from test_cli import run

from runon import cli, inventory
from runon.errors import ConfigError
from runon.inventory import Host
from runon.picker import ADD_NEW, choose_name
from runon.secrets import password_for


class TestAppendingHosts:
    def test_a_host_can_be_added_in_one_command(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, out, _ = run(
            ["add-host", "web-1", "--address", "10.0.0.1", "--user", "deploy"], tmp_path, capsys
        )

        assert code == 0
        inv = inventory.load(tmp_path / "inventory.toml")
        assert inv.hosts["web-1"].ssh_target == "deploy@10.0.0.1"
        assert "web-1" in out

    def test_what_was_already_in_the_file_is_untouched(self, tmp_path, capsys):
        """Appending, not rewriting.

        A parse-and-serialise round trip would drop every comment in a file
        this project asks you to read and diff in review.
        """
        path = tmp_path / "inventory.toml"
        original = (
            "# hand-written, do not lose me\n\n"
            "[hosts.web-1]\n"
            "address = \"10.0.0.1\"  # the old one\n"
        )
        path.write_text(original, encoding="utf-8")
        run(["init", str(tmp_path)], tmp_path, capsys)

        run(["add-host", "web-2", "--address", "10.0.0.2"], tmp_path, capsys)

        after = path.read_text(encoding="utf-8")
        assert after.startswith(original)
        assert "do not lose me" in after
        assert "# the old one" in after

    def test_a_duplicate_name_is_refused_rather_than_merged(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        run(["add-host", "web-1", "--address", "10.0.0.1"], tmp_path, capsys)

        code, _, err = run(["add-host", "web-1", "--address", "10.0.0.9"], tmp_path, capsys)

        assert code == 2
        assert "already has a host named 'web-1'" in err

    @pytest.mark.parametrize("name", ["web 1", "web.1", "", "[hosts.evil]"])
    def test_a_name_that_would_break_the_file_is_refused(self, name, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, _ = run(["add-host", name, "--address", "10.0.0.1"], tmp_path, capsys)

        assert code == 2
        assert inventory.load(tmp_path / "inventory.toml").hosts.keys() == set()

    def test_an_address_with_a_quote_cannot_break_the_file(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        run(["add-host", "odd", "--address", 'a"b\\c'], tmp_path, capsys)

        # the proof is that it still parses
        assert inventory.load(tmp_path / "inventory.toml").hosts["odd"].address == 'a"b\\c'

    def test_it_never_takes_a_password_on_the_command_line(self):
        # it would be in shell history, and from there in something committed
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["add-host", "web-1", "--password", "hunter2"])

    def test_without_a_terminal_a_missing_address_is_refused(self, tmp_path, capsys):
        run(["init", str(tmp_path)], tmp_path, capsys)
        code, _, err = run(["add-host", "web-1"], tmp_path, capsys)

        assert code == 2
        assert "--address" in err


class TestInlinePasswordsAreRefused:
    def _inventory(self, tmp_path, body: str):
        path = tmp_path / "inventory.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_password_in_the_inventory_is_an_error_not_a_warning(self, tmp_path):
        path = self._inventory(
            tmp_path, '[hosts.web-1]\naddress = "10.0.0.1"\npassword = "hunter2"\n'
        )
        with pytest.raises(ConfigError) as excinfo:
            inventory.load(path)

        # a warning scrolls past; the secret is committed either way
        assert "becomes a password in git" in str(excinfo.value)

    def test_a_reference_is_fine(self, tmp_path):
        path = self._inventory(
            tmp_path, '[hosts.web-1]\naddress = "10.0.0.1"\npassword_env = "WEB1"\n'
        )
        assert inventory.load(path).hosts["web-1"].password_env == "WEB1"


class TestResolvingAPassword:
    def test_from_an_environment_variable(self, monkeypatch):
        monkeypatch.setenv("WEB1_PASS", "from-env")
        assert password_for(Host("web-1", "a", password_env="WEB1_PASS")) == "from-env"

    def test_an_unset_variable_says_which_one(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        with pytest.raises(ConfigError) as excinfo:
            password_for(Host("web-1", "a", password_env="NOPE"))
        assert "$NOPE" in str(excinfo.value)

    def _secret(self, tmp_path, text="from-file\n", mode=0o600):
        path = tmp_path / "secret"
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_from_a_file(self, tmp_path):
        path = self._secret(tmp_path)
        assert password_for(Host("db-1", "a", password_file=str(path))) == "from-file"

    def test_a_readable_file_is_refused(self, tmp_path):
        path = self._secret(tmp_path, mode=0o644)
        with pytest.raises(ConfigError) as excinfo:
            password_for(Host("db-1", "a", password_file=str(path)))
        assert "chmod 600" in str(excinfo.value)
        assert stat.S_IMODE(path.stat().st_mode) == 0o644, "must not fix it silently"

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(ConfigError):
            password_for(Host("db-1", "a", password_file=str(tmp_path / "gone")))

    def test_an_empty_file_is_not_an_empty_password(self, tmp_path):
        with pytest.raises(ConfigError):
            password_for(Host("db-1", "a", password_file=str(self._secret(tmp_path, ""))))

    def test_keys_alone_when_nothing_is_named(self):
        assert password_for(Host("web-1", "a")) is None

    def test_ask_password_is_the_fallback(self):
        assert password_for(Host("web-1", "a"), "typed") == "typed"

    def test_what_the_host_names_beats_what_you_typed(self, monkeypatch):
        # the machine that says where its credential lives has already answered
        monkeypatch.setenv("WEB1_PASS", "from-env")
        assert password_for(Host("web-1", "a", password_env="WEB1_PASS"), "typed") == "from-env"


class TestGroupCredentials:
    def _inventory(self, tmp_path):
        path = tmp_path / "inventory.toml"
        path.write_text(
            '[hosts.web-1]\naddress = "10.0.0.1"\n'
            '[hosts.web-2]\naddress = "10.0.0.2"\npassword_env = "OWN"\n'
            '[groups.web]\nhosts = ["web-1", "web-2"]\npassword_env = "SHARED"\n',
            encoding="utf-8",
        )
        return inventory.load(path)

    def test_a_group_credential_reaches_members_without_one(self, tmp_path):
        hosts = {h.name: h for h in self._inventory(tmp_path).group("web")}
        assert hosts["web-1"].password_env == "SHARED"

    def test_a_host_that_names_its_own_keeps_it(self, tmp_path):
        hosts = {h.name: h for h in self._inventory(tmp_path).group("web")}
        assert hosts["web-2"].password_env == "OWN"


class TestUnattended:
    """The point of the whole feature: a run nobody is watching."""

    def test_a_password_reaches_ssh_with_nobody_at_the_keyboard(self, monkeypatch, tmp_path):
        from runon.transport import SSHTransport

        monkeypatch.setenv("WEB1_PASS", "from-env")
        host = Host("web-1", "10.0.0.1", password_env="WEB1_PASS")

        assert SSHTransport().password_for(host) == "from-env"

    def test_and_batchmode_is_dropped_only_for_that_host(self, monkeypatch):
        from runon.transport import SSHTransport

        monkeypatch.setenv("WEB1_PASS", "from-env")
        transport = SSHTransport(persist=None)

        with_password = transport._base(Host("web-1", "a", password_env="WEB1_PASS"), "ssh")
        without = transport._base(Host("web-2", "b"), "ssh")

        assert "BatchMode=yes" not in with_password
        # a host with no credential must still fail fast rather than hang
        assert "BatchMode=yes" in without


class TestPickerOffersToAddOne:
    def test_the_last_entry_adds_a_host(self):
        import io

        chosen = choose_name(
            "host", ["web-1"], stream=tty("2\n"), prompt_stream=io.StringIO(), offer_new=True
        )
        assert chosen == ADD_NEW

    def test_choosing_a_real_host_still_returns_its_name(self):
        import io

        chosen = choose_name(
            "host", ["web-1"], stream=tty("1\n"), prompt_stream=io.StringIO(), offer_new=True
        )
        assert chosen == "web-1"

    def test_an_empty_inventory_still_offers_it(self):
        import io

        out = io.StringIO()
        chosen = choose_name(
            "host", [], stream=tty("1\n"), prompt_stream=out, offer_new=True
        )
        assert chosen == ADD_NEW
        assert "add a new host" in out.getvalue()

    def test_the_marker_cannot_collide_with_a_real_name(self):
        with pytest.raises(ConfigError):
            inventory.validate_host_name(ADD_NEW)

    def test_append_host_validates_for_itself(self, tmp_path):
        """The CLI checks first, so this guard needs its own test.

        append_host is importable, and a caller that skipped the CLI could
        otherwise write a table header that breaks every later command.
        """
        path = tmp_path / "inventory.toml"
        path.write_text("[hosts.web-1]\naddress = \"10.0.0.1\"\n", encoding="utf-8")

        with pytest.raises(ConfigError):
            inventory.append_host(path, Host("web 1", "10.0.0.2"))

        assert inventory.load(path).hosts.keys() == {"web-1"}


class TestStoringAPasswordForYou:
    """`add-host` can take the password and write the file itself.

    Asking for a path assumed a file you had already made, with the right mode,
    in a directory that is not the committed workspace. Three chances to get it
    wrong before you have started.
    """

    def test_it_is_written_readable_only_by_you(self, tmp_path):
        from runon import secrets

        path = secrets.write_password_file("web-1", "s3cret")

        assert path.read_text(encoding="utf-8") == "s3cret"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_the_directory_is_yours_alone(self, tmp_path):
        from runon import secrets

        secrets.write_password_file("web-1", "s3cret")

        assert stat.S_IMODE(secrets.secrets_dir().stat().st_mode) == 0o700

    def test_an_existing_loose_directory_is_tightened(self, tmp_path):
        """An earlier umask, or a directory somebody made by hand."""
        from runon import config, secrets

        loose = config.home() / "secrets"
        loose.mkdir(parents=True)
        loose.chmod(0o755)

        secrets.write_password_file("web-1", "s3cret")

        assert stat.S_IMODE(loose.stat().st_mode) == 0o700

    def test_it_is_never_briefly_world_readable(self, tmp_path, monkeypatch):
        """open-then-chmod leaves a window where anyone can read it.

        The mode has to be applied by the open itself, so this checks the flags
        the file is created with rather than the mode it ends up at.
        """
        from runon import secrets

        seen = {}
        real_open = secrets.os.open

        def spy(path, flags, mode=0o777):
            seen["mode"] = mode
            return real_open(path, flags, mode)

        monkeypatch.setattr(secrets.os, "open", spy)
        secrets.write_password_file("web-1", "s3cret")

        assert seen["mode"] == 0o600

    def test_a_rewrite_does_not_inherit_a_loose_mode(self, tmp_path):
        """O_CREAT leaves an existing file's mode alone."""
        from runon import secrets

        path = secrets.write_password_file("web-1", "old")
        path.chmod(0o644)

        secrets.write_password_file("web-1", "new")

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_an_empty_password_is_refused(self, tmp_path):
        from runon import secrets

        with pytest.raises(ConfigError):
            secrets.write_password_file("web-1", "")

    def test_it_lives_outside_the_workspace(self, tmp_path, capsys):
        """The workspace is committed. That is the entire point."""
        from runon import config, secrets

        workspace = tmp_path / "ops"
        run(["init", str(workspace)], tmp_path, capsys)
        path = secrets.write_password_file("web-1", "s3cret")

        assert config.workspace().root.resolve() == workspace.resolve()
        assert workspace.resolve() not in path.resolve().parents

    def test_what_was_written_reads_back(self, tmp_path):
        from runon import secrets
        from runon.inventory import Host

        path = secrets.write_password_file("web-1", "s3cret")
        host = Host("web-1", "10.0.0.1", password_file=str(path))

        assert password_for(host) == "s3cret"


class TestPasswordFromStdin:
    """The scripted half, so automation never puts one in argv."""

    def test_it_stores_what_was_piped_and_records_the_path(
        self, tmp_path, monkeypatch, capsys
    ):
        import io

        from runon import config

        run(["init", str(tmp_path)], tmp_path, capsys)
        monkeypatch.setattr("sys.stdin", io.StringIO("from-a-pipe\n"))

        code, out, _ = run(
            ["add-host", "web-1", "--address", "10.0.0.1", "--password-stdin"], tmp_path, capsys
        )

        assert code == 0
        stored = config.home() / "secrets" / "web-1"
        assert stored.read_text(encoding="utf-8") == "from-a-pipe"
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600
        assert "0600" in out

    def test_the_inventory_records_the_path_and_not_the_password(
        self, tmp_path, monkeypatch, capsys
    ):
        import io

        run(["init", str(tmp_path)], tmp_path, capsys)
        monkeypatch.setattr("sys.stdin", io.StringIO("from-a-pipe\n"))
        run(["add-host", "web-1", "--address", "10.0.0.1", "--password-stdin"], tmp_path, capsys)

        written = (tmp_path / "inventory.toml").read_text(encoding="utf-8")

        assert "from-a-pipe" not in written
        assert "password_file" in written

    def test_there_is_still_no_password_flag(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["add-host", "web-1", "--password", "hunter2"]
            )
