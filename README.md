# runon

[![PyPI](https://img.shields.io/pypi/v/runon.svg)](https://pypi.org/project/runon/) [![Python](https://img.shields.io/pypi/pyversions/runon.svg)](https://pypi.org/project/runon/) [![CI](https://github.com/ahmed-hashim-pro/runon/actions/workflows/ci.yml/badge.svg)](https://github.com/ahmed-hashim-pro/runon/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

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
pip install runon            # or: pipx install runon

runon list programs                        # nothing to set up first
runon local run-program hello-world -v
```

## One workspace, remembered

> **Changed in 0.4.0.** The workspace has a fixed default and is created on
> first use, so there is nothing to run before `runon` works. **0.3.0** removed
> the upward search from your current directory — before that, the same command
> listed different programs depending on where you stood.

Your programs live in **one directory**, fixed unless you move it:

```
~/.runon/workspace/          # the one place, made on first use
├── programs/<name>/main.sh  # a program is a directory, so it can carry its own files
├── functions/               # shared helpers, shipped alongside every program
├── layouts/
└── inventory.toml           # hosts and groups

~/.runon/config.toml         # only if you move it
    workspace = "/Users/you/ops"
```

So `runon` means the same thing in every directory on the machine — there is
nothing to `cd` to and no per-directory surprises:

```bash
cd /anywhere
runon list programs                      # your programs
runon group --group prod run-program --program deploy
```

```bash
runon config                             # where am I pointed?
runon config --workspace ~/ops           # point somewhere that already exists
runon init ~/ops                         # scaffold and point, saying what it was
runon -C ~/scratch list programs         # just this once, without repointing
```

Precedence, in one place so it cannot drift: **`-C` beats the config file, and
nothing else is consulted.**

Set `RUNON_HOME` to keep a second, separate config — a work profile and a
personal one, say.

The `examples/` directory in this repo is a workspace you can try without
committing to it: `runon -C examples list programs`.

> That installs exactly one package. `runon` has no runtime dependencies, and
> the only external programs it uses are the `ssh` and `scp` you already have.

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

### Program parameters

Settings that belong to the program rather than to a host — a threshold, a
branch, a service name — go in a `params.toml` beside its `main.sh`:

```toml
# programs/disk-report/params.toml
threshold = 90
branch = "main"
```

They arrive as `RUNON_PARAM_THRESHOLD`, `RUNON_PARAM_BRANCH`, and they **travel
with the program** when it is copied — so a target always runs it with the
values it shipped with, rather than whatever the operator happened to type.

A program parameter beats a host variable of the same name: the program is the
more specific statement of intent.

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

## Watching it happen

For a long program, "2/3 ok" arriving five minutes later tells you much less
than seeing which host is stuck. `--watch` runs it in tmux with one pane per
host and attaches:

```bash
runon group --group production --watch copy-run-program --program migrate
```

Panes are tiled and kept open after the command exits — a pane that vanishes
takes the error message with it, which is exactly the moment you were watching
for. Detach with `Ctrl-b d`; the session name is printed so you can reattach.

Copying still happens up front and sequentially, because a pane whose first act
is failing to find the program is not showing you anything.

Needs `tmux`. Without `--watch`, results are collected and reported per host as
they finish.

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

## Authentication

`runon` never handles credentials itself for key-based access — it shells out to
your `ssh`, so your agent, your keys, your `~/.ssh/config` and your `known_hosts`
all apply unchanged.

**Keys are the default and the recommendation.** Set one up once and you never
type anything again:

```bash
ssh-copy-id deploy@web-1
```

**When you need a password**, ask for one explicitly:

```bash
runon host  --host root@10.0.0.4   --ask-password run-program --program setup
runon group --group staging        --ask-password copy-run-program --program deploy
```

You are prompted **once**, even for a group of twenty — the same credential is
used for every host in it, and being asked twenty times would be its own
argument against the feature. Keys are still attempted first, so any host that
already trusts your key never sees the password.

### How the password reaches ssh

Not through `sshpass`. That would be an extra binary that is not installed by
default anywhere, and `sshpass -p` puts the password in the process table where
any user on the machine can read it with `ps`.

Instead `runon` uses OpenSSH's own `SSH_ASKPASS`. The password is written to a
file only your user can read (`0600`), inside a directory only your user can
enter (`0700`), and both are deleted when the run ends — including when it ends
badly. It is never an argument, never an environment variable ssh passes on, and
never written to the audit of what ran.

Two consequences worth knowing:

- **One attempt per host.** `NumberOfPasswordPrompts=1`, because three failed
  prompts across twenty machines is a very slow way to learn you typed it wrong.
- **Without `--ask-password`, ssh is run with `BatchMode=yes`.** A host that
  does not have your key fails immediately rather than blocking on a prompt —
  across a group, the alternative is twenty stuck connections and no output.

If hosts in a group have *different* passwords, run them separately. Better: use
`ssh-copy-id` and stop typing passwords.

### Adding machines

Name one in a single command, for a script:

```bash
runon add-host web-1 --address 10.0.0.1 --user deploy
runon add-host db-1  -a 10.0.0.9 --port 2222 --password-file ~/.runon/secrets/db-1
```

Or leave things out and answer:

```
$ runon add-host
Name: web-9
Address (hostname or IP): 10.0.0.9
SSH user (blank for your own): deploy

How does it authenticate?
   1. ssh key (nothing to store)
   2. password in an environment variable
   3. password in a 0600 file
Select 1-3 [1]: 2
  Variable name: WEB9_PASS
  added web-9  (deploy@10.0.0.9)
```

The same prompt is the last entry of the host menu, because the moment you
notice a machine is missing is the moment you are looking at the list:

```
$ runon host run-program deploy
Which host?
   1. web-1
   2. web-2
   3. add a new host…
```

`add-host` **appends** to `inventory.toml` and never rewrites it, so your
comments and ordering survive. A duplicate name is refused rather than merged.

### Running with nobody watching

A password can be stored — as a *reference*, never as the secret:

```toml
[hosts.web-1]
address = "10.0.0.1"
password_env = "WEB1_PASS"          # read from the environment

[hosts.db-1]
address = "10.0.0.9"
password_file = "~/.runon/secrets/db-1"   # must be 0600

[groups.production]
hosts = ["web-1", "db-1"]
password_env = "PROD_PASS"          # for members that name nothing themselves
```

```bash
WEB1_PASS=… runon host --host web-1 run-program deploy    # no prompt, no tty
```

Three rules that are enforced, not documented:

- **`password = "..."` in the inventory is an error**, not a warning. That file
  is in your workspace and gets committed, so a secret written there is a
  secret pushed. The message says to use `password_env` or `password_file`.
- **A password file that is not `0600` is refused** with the `chmod` to run.
  Warning and continuing would leave it readable for exactly as long as it
  takes to ignore a warning.
- **`add-host` has no `--password` flag.** It would be in your shell history.

A host that names its own credential beats `--ask-password`; a group's is a
default for members that name nothing. Everything still goes through
`SSH_ASKPASS`, so no password reaches the process table.

### Not typing it every time

**The real answer is a key.** One `ssh-copy-id` per host and you never type
anything again — no prompt, no stored secret, no expiry to think about. If you
find yourself reaching for `--ask-password` twice on the same machine, that is
the signal.

For everything in between, `runon` reuses connections. The first command to a
host authenticates and leaves a master connection open; every command after it
travels down that socket and authenticates **not at all**:

```bash
runon host --host web-1 --ask-password copy-run-program --program deploy
runon host --host web-1 run-program --program smoke-test    # no prompt
```

This is on by default at 60 seconds, which is enough that the several
connections a single command makes — `copy-run-program` is at least two — share
one login instead of one each. Stretch it for a working session, or turn it off:

```bash
runon group --group staging --persist 10m  run-program --program tail-logs
runon host  --host web-1    --persist no   run-program --program deploy
```

It is OpenSSH's own `ControlMaster`, so nothing is stored: the state is a live
socket that closes itself when the timer runs out.

The tradeoff, stated plainly: **while that socket is open, anything that can
reach it can use your authenticated session without knowing your credential.**
The sockets live in `~/.runon/sockets` at `0700`, so on a single-user machine
that means you. On a shared box, or if you step away from an unlocked terminal,
prefer a short `--persist` — or a key and no password at all.

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

Script a failure to check the half that matters — `responses` matches on a
substring of the command, first match wins, and `default_exit` fails everything:

```python
fake = FakeTransport(responses={"migrate": Result("", "", 1, "", "lock held")})
```

## Programs that describe themselves

Two optional files beside `main.sh`. Neither changes what an existing program
does — a program without them behaves exactly as it did before they existed.

**`meta.toml`** — what it is:

```toml
title       = "Deploy the API"
description = "Ships the current build and restarts the service"
category    = "deploy"
status      = "active"          # or experimental, deprecated
destructive = true
confirm_message = "Restarts production and drops in-flight requests."
tags        = ["api", "prod"]
```

`description` is what `runon list programs` and the picker show. `destructive`
is agreed to before anything runs:

```
$ runon local run-program deploy
Deploy the API — Ships the current build and restarts the service

DESTRUCTIVE: Restarts production and drops in-flight requests.
Run deploy anyway? [y/N]:
```

With nobody watching it **refuses** rather than agreeing on your behalf. Say
you mean it with `--yes` (or `-y`), or `RUNON_ASSUME_YES=1` for a whole
scheduled environment:

```bash
runon group --group production run-program migrate --yes
```

The warning is printed either way, including when `--yes` skips the question — a
log that does not say what it agreed to cannot tell you why the database is
gone.

**`prompts.toml`** — what it asks for:

```toml
[[prompt]]
key     = "branch"
title   = "Branch to deploy"
default = "main"

[[prompt]]
key    = "token"
title  = "Deploy token"
secret = true            # read with getpass, never echoed
```

Answers reach the script as `RUNON_PROMPT_BRANCH`, `RUNON_PROMPT_TOKEN`. A list
of tables rather than one table because an interview reads in an order and a
TOML table has none.

```
$ runon local run-program deploy
Branch to deploy [main]: hotfix
Deploy token:
```

Asked **once** for a whole group, not once per host.

### The same command, scheduled

```bash
RUNON_PROMPT_BRANCH=hotfix RUNON_PROMPT_TOKEN=… \
  runon group --group production run-program deploy --yes
```

Precedence is **environment → what you type → the declared default**. The
environment wins even when someone is watching: a value passed on purpose
should not be asked for again, and that is what makes one command work both by
hand and from cron. With no terminal and no default, it stops and names the
variable that would have supplied it:

```
runon: this program asks for 'Deploy token', which has no default,
and there is no terminal to ask on.
Set RUNON_PROMPT_TOKEN in the environment.
```

Full precedence for everything a program can see, most specific last:

```
RUNON_VAR_*      the host's vars
RUNON_PARAM_*    the program's params.toml
RUNON_PROMPT_*   answers given for this run
```

### Making one

`runon new-program <name>` asks what it is, and writes the files:

```
$ runon new-program deploy
Describe deploy. Enter skips anything.
  Title: Deploy the API
  One-line description: Ships the current build
  Category (deploy, checks, maintenance…): deploy
  Status [active/experimental/deprecated] (active):
  Destructive — hard to undo? [y/N]: y
  What should it warn before running? Restarts production.
  Tags (comma-separated): api, prod

  Does it ask for anything at run time? [y/N]: y
  Each becomes RUNON_PROMPT_<KEY> for the script. Blank key to finish.
    key: branch
    question: Branch to deploy
    default: main
    secret — hide while typing? [y/N]: n
    key:
```

Every answer is optional. With no terminal it writes `main.sh` alone, so
`runon new-program x` still works in a script.

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
runon init [DIR]                    scaffold a workspace and remember it
                                    (default: ~/.runon/workspace)
runon config [--workspace DIR]      show or change which one
runon add-host [NAME] [-a ADDR] [-u USER] [--port N]
               [--password-env VAR | --password-file PATH]
runon new-program <name>            create one from the template
runon list programs|hosts|groups|layouts
runon doctor                        check this machine has what runon needs
runon completion bash|zsh|fish      print a completion script

runon local run-program  [P] [args...]     (or --program P)
    -y/--yes   agree to a destructive program in advance
runon local run-layout   [--layout L]

runon host  [--host H]  [flags] <verb> [P] [args...]
runon group [--group G] [flags] <verb> [P] [-j N] [args...]

  flags: --ask-password  --persist D  --watch  --headless  --dry-run  --verbose
```

Omit `--program`, `--host` or `--group` and you get a menu — on a terminal.
With nothing to ask on (cron, CI, a pipe) runon refuses and names the choices,
rather than reaching EOF, calling it "cancelled", and exiting 0 having done
nothing:

```
$ runon group run-program --program deploy < /dev/null
runon: --group was not given and there is no terminal to ask on.
Pass --group explicitly. Choices: production, staging
$ echo $?
2
```

### Choosing a program

Leave `--program` out on a terminal and you get a picker: category tabs, a
Recent tab, type-to-filter, and a preview of what the selected one does.

```
Which program?
 All   Recent   checks   data   deploy

 › db-backup   [destructive]  — Dump and upload
   deploy                     — Ship the current build
   disk-report [experimental] — Check free space
   rollback                   — Put the last build back

  Dump and upload
  ↑↓ move   ←→ category   type to filter   ⏎ select   esc cancel
```

Categories, notes and previews come from each program's `meta.toml`; Recent is
the last ten you ran, kept in `~/.runon/config.toml`.

It is written against the terminal directly rather than with a library —
`runon` has no runtime dependencies, and drawing a list is not a good reason to
acquire one that has to be present on every machine anyone installs this on.

Nothing about it is load-bearing. No terminal, no `termios`, or any error while
drawing, and you get the numbered menu instead. `RUNON_PLAIN=1` forces the
menu if you prefer it.

Shell completion knows the scopes and verbs, and asks `runon` itself for
program, host, group and layout names — right after the verb, where you would
type them:

```
$ runon local run-program <TAB>
deploy  disk-report  hello-world

$ runon group --group prod copy-run-program de<TAB>
deploy
```

```bash
runon completion zsh > "${fpath[1]}/_runon"     # then: compinit
runon completion bash > /usr/local/etc/bash_completion.d/runon
runon completion fish > ~/.config/fish/completions/runon.fish
```

This is why `runon list` prints only names on stdout and puts everything else
on stderr: the completion scripts parse that output, so an explanation there
would be offered to you as a program name.

## What this does not do

- **No rollback, no idempotency, no desired-state model.** It runs your script.
  If you need convergence, you need Ansible or Chef, and you should use them.
- **No secrets management.** Put credentials in your own vault and have the
  program fetch them; `runon` never asks for or stores one.
- **No inventory discovery.** No cloud APIs, no dynamic inventory — you write
  the file.
- **No output streaming in the collected path.** Results arrive when a host
  finishes, not as it goes. Use `--watch` when you need to see it live.
- **Groups run over ssh only.** There is no agent to install, and no plan to add
  one.

## Tests
126 tests. No servers, no SSH keys, no network.

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
