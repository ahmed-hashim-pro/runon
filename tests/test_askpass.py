"""The password path, including the parts that decide whether it is safe."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from runon.askpass import askpass_env
from runon.inventory import Host
from runon.transport import SSHTransport

SECRET = "hunter2-with spaces-and'quotes"


PASSWORD_PROMPT = "medo@10.0.0.1's password: "


def test_the_helper_prints_the_password_ssh_asked_for():
    with askpass_env(SECRET) as env:
        # ssh execs this helper with the prompt as argv[1]; run it the same way.
        output = subprocess.run(
            [env["SSH_ASKPASS"], PASSWORD_PROMPT], capture_output=True, text=True, check=True
        )
    assert output.stdout == SECRET


def test_a_password_containing_shell_metacharacters_survives():
    nasty = "a b; rm -rf /$(whoami)`id`\"'"
    with askpass_env(nasty) as env:
        output = subprocess.run(
            [env["SSH_ASKPASS"], PASSWORD_PROMPT], capture_output=True, text=True, check=True
        )
    assert output.stdout == nasty


def test_nobody_else_can_read_the_password():
    with askpass_env(SECRET) as env:
        helper = Path(env["SSH_ASKPASS"])
        directory = helper.parent
        secret = directory / "secret"

        # 0600 on the file and 0700 on the directory: no group or other bits.
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(helper.stat().st_mode) == 0o700


def test_the_password_does_not_outlive_the_run():
    with askpass_env(SECRET) as env:
        directory = Path(env["SSH_ASKPASS"]).parent
        assert directory.exists()
    assert not directory.exists()


def test_it_is_cleaned_up_even_when_the_run_raises():
    directory = None
    try:
        with askpass_env(SECRET) as env:
            directory = Path(env["SSH_ASKPASS"]).parent
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert directory is not None and not directory.exists()


def test_force_is_set_so_ssh_uses_the_helper_even_with_a_terminal():
    with askpass_env(SECRET) as env:
        assert env["SSH_ASKPASS_REQUIRE"] == "force"
        assert env["DISPLAY"]


def test_the_password_is_never_an_argument():
    # sshpass -p puts the password in the process table, where any user on the
    # machine can read it with ps. Nothing here should appear in argv.
    with askpass_env(SECRET) as env:
        assert SECRET not in " ".join(f"{k}={v}" for k, v in env.items())


class TestArgvChanges:
    HOST = Host(name="h", address="example.com")

    def test_without_a_password_it_refuses_to_prompt(self):
        argv = " ".join(SSHTransport()._base(self.HOST, "ssh"))
        assert "BatchMode=yes" in argv

    def test_with_a_password_prompting_is_allowed(self):
        argv = " ".join(SSHTransport(password=SECRET)._base(self.HOST, "ssh"))
        # BatchMode=yes would defeat the whole feature
        assert "BatchMode=yes" not in argv

    def test_only_one_attempt_per_host(self):
        argv = " ".join(SSHTransport(password=SECRET)._base(self.HOST, "ssh"))
        # three prompts per host makes a wrong password a very slow discovery
        assert "NumberOfPasswordPrompts=1" in argv

    def test_the_password_is_not_in_the_command_line(self):
        for binary in ("ssh", "scp"):
            argv = SSHTransport(password=SECRET)._base(self.HOST, binary)
            assert not any(SECRET in part for part in argv)


class TestItOnlyAnswersPasswords:
    """ssh asks this helper about more than passwords.

    An unknown host key asks "Are you sure you want to continue connecting?".
    Answered with a password, ssh rejects it and asks again — forever. On CI
    that showed up as a run that hung for the full 3600s timeout and reported
    nothing useful, and it would do the same on the first connection to any
    host with a stored password.
    """

    def _ask(self, prompt: str):
        import subprocess

        from runon.askpass import askpass_env

        with askpass_env("s3cret") as env:
            return subprocess.run(
                [env["SSH_ASKPASS"], prompt], capture_output=True, text=True
            )

    @pytest.mark.parametrize(
        "prompt",
        [
            "medo@192.168.50.64's password: ",
            "Enter passphrase for key '/home/medo/.ssh/id_ed25519': ",
        ],
    )
    def test_a_password_prompt_is_answered(self, prompt):
        result = self._ask(prompt)

        assert result.returncode == 0
        assert result.stdout == "s3cret"

    @pytest.mark.parametrize(
        "prompt",
        [
            "Are you sure you want to continue connecting (yes/no/[fingerprint])? ",
            "The authenticity of host '[127.0.0.1]:2222' can't be established.",
            "Please type 'yes', 'no' or the fingerprint: ",
        ],
    )
    def test_anything_else_is_declined(self, prompt):
        result = self._ask(prompt)

        assert result.returncode != 0
        assert "s3cret" not in result.stdout

    def test_the_password_is_never_offered_to_a_confirmation(self):
        """The loop is the symptom; this is the thing that caused it."""
        result = self._ask("Are you sure you want to continue connecting (yes/no)? ")

        assert result.stdout.strip() == ""
