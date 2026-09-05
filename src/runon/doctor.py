"""Checking that the machine can do what runon is about to ask of it.

The original tool had an `install_required_packages` command. runon installs
nothing — it has no runtime dependencies and uses tools you already have — so
the useful version of that command is one that tells you what is missing and
what to do about it, rather than reaching for your package manager.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool


def _version(binary: str, *args: str) -> str:
    try:
        out = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""


def run_checks() -> list[Check]:
    checks: list[Check] = []

    ssh = shutil.which("ssh")
    checks.append(
        Check(
            "ssh",
            ssh is not None,
            _version("ssh", "-V") if ssh else "not found — remote commands cannot run",
            required=True,
        )
    )

    scp = shutil.which("scp")
    checks.append(
        Check("scp", scp is not None, scp or "not found — copying cannot work", required=True)
    )

    tmux = shutil.which("tmux")
    checks.append(
        Check(
            "tmux",
            tmux is not None,
            _version("tmux", "-V") if tmux else "not found — --watch and run-layout need it",
            required=False,
        )
    )

    agent = shutil.which("ssh-add")
    loaded = ""
    if agent:
        out = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, check=False)
        if out.returncode == 0:
            loaded = f"{len(out.stdout.strip().splitlines())} key(s) loaded"
        else:
            loaded = "no keys loaded — you will be asked for passwords"
    checks.append(Check("ssh-agent", bool(loaded and "no keys" not in loaded), loaded, False))

    copy_id = shutil.which("ssh-copy-id")
    checks.append(
        Check(
            "ssh-copy-id",
            copy_id is not None,
            copy_id or "not found — install it to stop typing passwords",
            required=False,
        )
    )

    checks.append(_on_path_check())
    checks.extend(_completion_checks())
    return checks


def _on_path_check() -> Check:
    """Whether `runon` is on PATH, which completion needs to ask it anything.

    The scripts shell out to `runon list programs` for names. Installed in a
    virtualenv you have not activated, the command works because you typed its
    full path and the completion finds nothing, which looks like the completion
    being broken.
    """
    found = shutil.which("runon")
    return Check(
        "runon on PATH",
        found is not None,
        found or "not on PATH — completion cannot ask it for program names",
        required=False,
    )


def _completion_checks() -> list[Check]:
    from . import completion

    shell = completion.default_shell()
    if shell is None:
        return [
            Check(
                "completion",
                False,
                f"$SHELL is {os.environ.get('SHELL') or 'unset'}; "
                "run: runon completion bash|zsh|fish --install",
                required=False,
            )
        ]

    checks = [_installed_check(shell, completion)]
    if shell == "bash":
        checks.append(_bash_completion_check())
    if shell == "zsh":
        checks.append(_zsh_fpath_check(completion))
    return checks


def _installed_check(shell: str, completion) -> Check:
    for candidate in _completion_candidates(shell, completion):
        if candidate.is_file():
            return Check(f"{shell} completion", True, str(candidate), required=False)
    return Check(
        f"{shell} completion",
        False,
        "not installed — run: runon completion --install",
        required=False,
    )


def _completion_candidates(shell: str, completion) -> list[Path]:
    """Everywhere a completion for this shell could have been put.

    Both the automatic install and an explicit one, plus the copy the wheel
    ships for a system-wide install, because "is it installed" has three
    possible answers and only one of them is the one runon would write.
    """
    seen = [completion.install_path(shell, user_only=True), completion.install_path(shell)]
    name = {"bash": "runon", "zsh": "_runon", "fish": "runon.fish"}[shell]
    subdir = {
        "bash": "bash-completion/completions",
        "zsh": "zsh/site-functions",
        "fish": "fish/vendor_completions.d",
    }[shell]
    for prefix in (sys.prefix, "/usr/local", "/usr", Path.home() / ".local"):
        seen.append(Path(prefix) / "share" / subdir / name)
    return seen


def _bash_completion_check() -> Check:
    """bash reads the user directory only when bash-completion is installed.

    Without it the file is in the right place and nothing loads it, which is
    the most confusing way for this to fail.
    """
    for candidate in (
        "/usr/share/bash-completion/bash_completion",
        "/etc/bash_completion",
        "/opt/homebrew/etc/profile.d/bash_completion.sh",
        "/usr/local/etc/profile.d/bash_completion.sh",
    ):
        if Path(candidate).is_file():
            return Check("bash-completion", True, candidate, required=False)
    return Check(
        "bash-completion",
        False,
        "not found — bash will not load the file. Install it: "
        "sudo apt install bash-completion",
        required=False,
    )


def _zsh_fpath_check(completion) -> Check:
    """Whether the directory holding the completion is somewhere zsh looks.

    zsh has no user completion directory by default, so a file in ~/.zsh needs
    a line in .zshrc before anything reads it.
    """
    installed = completion.install_path("zsh", user_only=True)
    if not installed.is_file():
        for site in completion.ZSH_SITE_DIRS:
            if (Path(site) / "_runon").is_file():
                return Check("zsh fpath", True, f"{site} is on the default fpath", required=False)
        return Check("zsh fpath", False, "no completion installed yet", required=False)
    return Check(
        "zsh fpath",
        False,
        f"add to ~/.zshrc above compinit:  fpath=({installed.parent} $fpath)",
        required=False,
    )


def report(checks: list[Check], *, stream) -> int:
    """Prints the checks and returns non-zero only if something required is missing."""
    missing_required = 0
    for check in checks:
        if check.ok:
            mark = "ok  "
        elif check.required:
            mark = "MISSING"
            missing_required += 1
        else:
            mark = "--  "
        print(f"  {mark:<8} {check.name:<12} {check.detail}", file=stream)

    if missing_required:
        print("\nrunon cannot reach remote hosts without the missing tools above.", file=stream)
    return 1 if missing_required else 0
