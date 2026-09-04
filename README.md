# runon

[![CI](https://github.com/ahmed-hashim-pro/runon/actions/workflows/ci.yml/badge.svg)](https://github.com/ahmed-hashim-pro/runon/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Keep your operational procedures as plain shell scripts, and run any of them
identically on your laptop, on one server, or across a named group of servers.

Most teams end up with a `scripts/` folder nobody trusts and a wiki page that
went stale months ago. The alternatives are heavy: Ansible wants YAML, modules
and a mental model, which is a lot to adopt when the thing you actually have is
six shell scripts that work.

`runon` keeps the shell scripts. It adds the part that is genuinely annoying
to write yourself — getting a script and its helpers onto twenty machines,
running them, and telling you which ones failed.

```
$ runon group --group production copy-run-program --program disk-report
web-1                    ok
web-2                    ok
db-1                     FAILED (1)
    [db-1] highest usage: 94% on /var
    [db-1] OVER THRESHOLD

2/3 ok
```

Exit code is non-zero, because a rollout that worked on two of three machines
has not worked.

## Quickstart

Python 3.11+. No runtime dependencies.

```bash
pipx install git+https://github.com/ahmed-hashim-pro/runon.git
# or, into a virtualenv you manage:
#   pip install git+https://github.com/ahmed-hashim-pro/runon.git

mkdir my-ops && cd my-ops
runon init                 # scaffolds programs/, functions/, layouts/, inventory.toml
runon list programs
runon local run-program --program hello-world --verbose
```

> Not on PyPI yet, so install from the repository for now. Nothing else is
> needed — `runon` has no runtime dependencies, and the only external programs
> it uses are the `ssh` and `scp` you already have.

<details>
<summary>Releasing (for maintainers)</summary>

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/):
GitHub Actions mints a short-lived OIDC token, so there is no API token stored
anywhere to leak.

```bash
# rehearse against TestPyPI first
gh workflow run release.yml -f target=testpypi

# then release for real
git tag v0.1.0 && git push origin v0.1.0
```

The workflow runs the suite, builds an sdist and a wheel, checks the metadata,
and refuses to publish if the tag does not match the version in `pyproject.toml`.

</details>

That last command works immediately, with no servers and no configuration.

## The idea

A **program** is a directory with a `main.sh`:

```
programs/
  disk-report/
    main.sh          <- entry point
functions/
  say.sh             <- shared helpers, sourced by programs
layouts/
  split.sh           <- terminal layouts for the local machine
inventory.toml       <- your machines
```

Adding a capability means adding a directory. **The tool never changes.** That
is the whole design: `runon` knows how to *reach* machines, shell knows what
to *do* on them, and neither has to learn the other's job.

Programs get their context from the environment, so they stay runnable by hand:

| Variable | Is |
| --- | --- |
| `RUNON_HOST` | the host's name from the inventory |
| `RUNON_ADDRESS` | what ssh was given |
| `RUNON_PROGRAM` | the program's own name |
| `RUNON_FUNCTIONS` | where the helpers are — locally, your workspace; remotely, the copied cache |
| `RUNON_VAR_*` | anything you put in that host's `vars` |

Export those and `./main.sh` behaves exactly as `runon` would run it. That
matters at 3am.

## Three scopes, the same verbs

Where the work happens and what the work is are separate questions, so they are
separate parts of the command line:

```bash
runon local run-program --program disk-report
runon host  --host web-1        run-program --program disk-report
runon group --group production  run-program --program disk-report -j 8
```

The remote scopes share four verbs:

| Verb | Does |
| --- | --- |
| `copy` | copy a local file or directory (`--local-dir`, `--remote-dir`) |
| `copy-program` | copy a program **and the functions library** to the target(s) |
| `run-program` | run an already-copied program |
| `copy-run-program` | both, in one step |

`copy-program` ships the functions library alongside the program deliberately: a
program that sources a helper is broken without it, and the target is the worst
place to discover that.

Omit `--program` and you get a picker. Add `--dry-run` to see which hosts would
be touched without touching them.

## Inventory

One file, so you can read the whole thing in one screen and diff it in review:

```toml
[hosts.web-1]
address = "web-1.example.com"
user = "deploy"
vars = { role = "web" }

[hosts.db-1]
address = "10.0.0.9"
port = 2222

[groups.production]
hosts = ["web-1", "db-1"]
```

`address` is handed to ssh untouched, so a `Host` alias from your `~/.ssh/config`
works here. And `--host` falls back to treating an unknown name as an address,
so `--host root@10.0.0.4` needs no inventory entry at all.

A group naming a host that does not exist fails when the inventory loads —
before a rollout has half-finished on the hosts it could resolve.

## It uses your ssh, on purpose

`runon` shells out to the system `ssh` and `scp`. It does **not** embed an SSH
library.

That means your `~/.ssh/config`, your agent, your keys, your `ProxyJump` and
your `known_hosts` all work exactly as they already do, and `runon` never has
to grow its own half-version of any of it. Connections run with `BatchMode=yes`,
so a missing key fails fast instead of hanging on a password prompt — which
across a group would otherwise mean twenty stuck connections.

One thing worth knowing: OpenSSH 9 moved `scp` onto the SFTP subsystem, so a
target with SFTP disabled fails a copy with an error that does not say so.
`runon` detects that and tells you the fix.

## Testing your programs without servers

Everything that touches another machine goes through one `Transport` interface,
and the fake one is **public API** rather than a test fixture — the hard part of
adopting a tool like this is proving your programs do the right thing *before*
you point them at production:

```python
from runon import FakeTransport, Host, Workspace
from runon import runner

fake = FakeTransport()
runner.run_program(fake, Host("web-1", "web-1.example.com"), workspace, program)

fake.calls    # [("web-1", "cd ~/.runon/programs/... && ./main.sh")]
fake.copies   # what would have been shipped
```

## Writing programs

Conventions that keep this pleasant, learned the hard way:

- **One job per function file.** `clone.sh` and `build.sh`, not `do_everything.sh`.
- **Functions do not call functions.** A call stack you have to unpick over ssh
  at 3am is a call stack too deep. Keep the depth at one.
- **The first comment line is the description.** `runon list programs` shows
  it, so it cannot drift out of date the way a separate metadata file would.
- **Take arguments, don't hardcode.** Arguments after the program name are
  passed through, quoted: `run-program --program disk-report 80`.

## Commands

```
runon init                          scaffold a workspace here
runon new-program <name>            create one from the template
runon list programs|hosts|groups|layouts

runon local run-program  [--program P] [args...]
runon local run-layout   [--layout L]

runon host  --host H  <verb> [--program P] [args...]
runon group --group G <verb> [--program P] [-j N] [args...]
```

## What this does not do

- **No rollback, no idempotency, no desired-state model.** It runs your script.
  If you need convergence, you need Ansible or Chef, and you should use them.
- **No secrets management.** Put credentials in your own vault and have the
  program fetch them; `runon` never asks for or stores one.
- **No inventory discovery.** No cloud APIs, no dynamic inventory — you write
  the file.
- **No output streaming.** Results arrive when a host finishes, not as it goes.
  For a long program, watch it on one host first.
- **Groups run over ssh only.** There is no agent to install, and no plan to add
  one.

## Tests

60 tests. No servers, no SSH keys, no network.

```bash
pip install -e ".[dev]"
pytest
```

CI runs them on Linux and macOS across Python 3.11–3.13, then executes the
quickstart above from an empty directory — so `init` producing something that
actually runs is checked on every commit, rather than being discovered by the
first user.

## License

MIT — see [LICENSE](LICENSE).
