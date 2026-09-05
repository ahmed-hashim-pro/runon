from __future__ import annotations

import io
from pathlib import Path

import pytest

from runon.program import Workspace
from runon.scaffold import write_workspace


@pytest.fixture(autouse=True)
def _home_of_its_own(tmp_path, monkeypatch):
    """Every test gets its own home directory.

    Without this the suite writes a real config to the developer's home and
    silently repoints their workspace — and it would pass on CI, where the home
    directory is disposable, so nobody would find out there.

    ZSH_SITE_DIRS is neutralised too: those are absolute system paths, so
    HOME does not isolate them and a test would write a completion into
    /opt/homebrew or /usr/share. Tests that want that path set it themselves.
    """
    from runon import completion

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RUNON_HOME", str(tmp_path / "runon-home"))
    monkeypatch.setattr(completion, "ZSH_SITE_DIRS", ())


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """A scaffolded workspace, which is also what a new user gets."""
    write_workspace(tmp_path)
    return Workspace(root=tmp_path)


@pytest.fixture
def inventory_file(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.toml"
    path.write_text(
        """
[hosts.web-1]
address = "web-1.example.com"
user = "deploy"
vars = { role = "web" }

[hosts.web-2]
address = "web-2.example.com"
user = "deploy"

[hosts.db-1]
address = "10.0.0.9"
port = 2222

[groups.web]
hosts = ["web-1", "web-2"]

[groups.all]
hosts = ["web-1", "web-2", "db-1"]
""",
        encoding="utf-8",
    )
    return path


class tty(io.StringIO):
    """A StringIO that claims to be a terminal.

    The pickers refuse to prompt when nobody is there to answer, and a plain
    StringIO is not a terminal, so every test that drives a menu needs this.
    """

    def isatty(self) -> bool:
        return True
