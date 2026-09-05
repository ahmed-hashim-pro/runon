"""What doctor says about completion, which is the thing people report broken.

Every failure mode here looks identical from the outside — you press tab and
nothing happens — so the useful check is the one that says which of them it is.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from runon import completion, doctor


def _check(checks, name):
    return next(c for c in checks if c.name == name)


def _names(checks):
    return [c.name for c in checks]


@pytest.fixture(autouse=True)
def _bash(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")


class TestIsItInstalled:
    def test_it_says_where_when_it_is(self, tmp_path):
        path = completion.install_path("bash", user_only=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#", encoding="utf-8")

        assert _check(doctor.run_checks(), "bash completion").ok

    def test_it_says_what_to_run_when_it_is_not(self, tmp_path):
        check = _check(doctor.run_checks(), "bash completion")

        assert not check.ok
        assert "runon completion --install" in check.detail

    def test_a_system_wide_copy_counts(self, tmp_path, monkeypatch):
        """The wheel ships one for system installs; it is installed either way."""
        prefix = tmp_path / "usr"
        shipped = prefix / "share/bash-completion/completions/runon"
        shipped.parent.mkdir(parents=True)
        shipped.write_text("#", encoding="utf-8")
        monkeypatch.setattr(doctor.sys, "prefix", str(prefix))

        assert _check(doctor.run_checks(), "bash completion").ok


class TestTheThingsThatSilentlyStopItWorking:
    def test_bash_without_bash_completion_is_named(self, tmp_path, monkeypatch):
        """The file is in the right place and nothing loads it.

        This is the most confusing failure of the lot, because everything runon
        can see is correct.
        """
        monkeypatch.setattr(doctor, "Path", _NoSuchPath(tmp_path))

        check = _check(doctor.run_checks(), "bash-completion")

        assert not check.ok
        assert "apt install bash-completion" in check.detail

    def test_zsh_is_told_about_fpath(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", ())
        path = completion.install_path("zsh", user_only=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#", encoding="utf-8")

        check = _check(doctor.run_checks(), "zsh fpath")

        assert not check.ok
        assert "fpath=" in check.detail and ".zshrc" in check.detail

    def test_a_site_directory_needs_no_fpath_line(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        site = tmp_path / "site-functions"
        site.mkdir()
        (site / "_runon").write_text("#", encoding="utf-8")
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", (str(site),))

        assert _check(doctor.run_checks(), "zsh fpath").ok

    def test_runon_missing_from_path_is_named(self, monkeypatch):
        """Completion asks `runon list programs` for names.

        In a virtualenv you have not activated, the command still works because
        you typed its path — and the completion finds nothing, which reads as
        the completion being broken.
        """
        monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

        check = _check(doctor.run_checks(), "runon on PATH")

        assert not check.ok
        assert "completion cannot ask it" in check.detail

    def test_an_unknown_shell_says_what_to_run(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/nonesuch")

        check = _check(doctor.run_checks(), "completion")

        assert not check.ok
        assert "bash|zsh|fish" in check.detail


class TestItStaysAdvice:
    def test_none_of_it_makes_doctor_fail(self, monkeypatch):
        """Missing completion is not a broken machine.

        doctor's exit code means "runon cannot reach hosts", and a shell that
        does not complete has nothing to do with that.
        """
        monkeypatch.setenv("SHELL", "/bin/nonesuch")
        checks = [c for c in doctor.run_checks() if not c.name.startswith(("ssh", "scp", "tmux"))]

        assert all(not c.required for c in checks)

    def test_the_report_lists_them(self, monkeypatch):
        stream = io.StringIO()
        doctor.report(doctor.run_checks(), stream=stream)

        assert "completion" in stream.getvalue()
        assert "runon on PATH" in stream.getvalue()


class _NoSuchPath:
    """Path, but nothing under the system directories exists.

    Simulates a machine with no bash-completion package without needing one.
    """

    def __init__(self, tmp_path):
        self._tmp = tmp_path

    def __call__(self, value):
        real = Path(value)
        if str(real).startswith(("/usr", "/etc", "/opt")):
            return self._tmp / "absent" / str(real).lstrip("/")
        return real

    def home(self):
        return Path.home()


class TestTheShellRemembersNoCompletion:
    """bash-completion caches a negative, and that is the whole failure.

    __load_completion falls through to `complete -F _minimal -- runon` when no
    file exists yet. That registration lasts for the life of the shell, so
    pressing tab once before installing — which is exactly what somebody does
    after `pip install` — leaves that shell completing filenames forever.
    """

    def test_the_install_message_says_to_open_a_new_shell(self, tmp_path):
        _, remaining = completion.install("bash")

        assert "new shell" in remaining
        assert "already decided" in remaining

    def test_zsh_on_the_default_fpath_says_it_too(self, tmp_path, monkeypatch):
        site = tmp_path / "site"
        site.mkdir()
        monkeypatch.setattr(completion, "ZSH_SITE_DIRS", (str(site),))

        _, remaining = completion.install("zsh")

        assert "new shell" in remaining

    def test_doctor_says_it_even_when_everything_is_correct(self, tmp_path):
        path = completion.install_path("bash", user_only=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#", encoding="utf-8")

        check = _check(doctor.run_checks(), "bash completion")

        # "ok" with tab still doing nothing is the report we got
        assert check.ok
        assert "restarting" in check.detail
