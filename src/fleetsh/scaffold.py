"""Writing a starting workspace, so a new user has something that runs."""

from __future__ import annotations

from pathlib import Path

from .errors import ProgramInvalid

PROGRAM_TEMPLATE = """#!/usr/bin/env sh
# {description}
set -eu

# fleetsh exports these; they also let you run this script by hand.
echo "program : ${{FLEETSH_PROGRAM:-{name}}}"
echo "host    : ${{FLEETSH_HOST:-local}}"

# Shared helpers live next door. Keep each one to a single job, and do not have
# them call each other — a function that calls a function is a call stack you
# will be debugging over ssh at some point.
. "${{FLEETSH_FUNCTIONS:-$(cd "$(dirname "$0")/../../functions" && pwd)}}/say.sh"

say "hello from $(hostname)"
"""

SAY_FUNCTION = """#!/usr/bin/env sh
# Prints a message with a consistent prefix. One job, no nesting.
say() {
    printf '[%s] %s\\n' "${FLEETSH_HOST:-local}" "$1"
}
"""

INVENTORY_TEMPLATE = """# Machines fleetsh can reach.
#
# `address` is handed to ssh untouched, so a Host alias from your ~/.ssh/config
# works here, and so does user@1.2.3.4.

# [hosts.web-1]
# address = "web-1.example.com"
# user = "deploy"
# vars = { role = "web" }

# [hosts.db-1]
# address = "db-1.example.com"
# user = "deploy"

# [groups.production]
# hosts = ["web-1", "db-1"]
"""

LAYOUT_TEMPLATE = """#!/usr/bin/env sh
# Opens a tmux session with the panes you always want together.
set -eu

SESSION="${1:-work}"
command -v tmux >/dev/null || { echo "tmux is not installed"; exit 1; }

tmux new-session -d -s "$SESSION" 2>/dev/null || true
tmux split-window -h -t "$SESSION" 2>/dev/null || true
echo "attach with: tmux attach -t $SESSION"
"""


def write_program(programs_dir: Path, name: str, description: str | None = None) -> Path:
    directory = programs_dir / name
    if directory.exists():
        raise ProgramInvalid(f"{directory} already exists")
    directory.mkdir(parents=True)
    entry = directory / "main.sh"
    entry.write_text(
        PROGRAM_TEMPLATE.format(name=name, description=description or f"{name} program"),
        encoding="utf-8",
    )
    entry.chmod(0o755)
    return entry


def write_workspace(root: Path, *, force: bool = False) -> list[Path]:
    programs = root / "programs"
    functions = root / "functions"
    layouts = root / "layouts"
    inventory = root / "inventory.toml"

    if programs.exists() and not force:
        raise ProgramInvalid(f"{programs} already exists; pass --force to add to it")

    created: list[Path] = []
    for directory in (programs, functions, layouts):
        directory.mkdir(parents=True, exist_ok=True)

    say = functions / "say.sh"
    if not say.exists():
        say.write_text(SAY_FUNCTION, encoding="utf-8")
        say.chmod(0o755)
        created.append(say)

    hello = programs / "hello-world"
    if not hello.exists():
        created.append(write_program(programs, "hello-world", "Prints a greeting from each host."))

    layout = layouts / "split.sh"
    if not layout.exists():
        layout.write_text(LAYOUT_TEMPLATE, encoding="utf-8")
        layout.chmod(0o755)
        created.append(layout)

    if not inventory.exists():
        inventory.write_text(INVENTORY_TEMPLATE, encoding="utf-8")
        created.append(inventory)

    return created
