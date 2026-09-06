# runon — end-to-end acceptance test on a real Ubuntu machine

You are testing a published CLI called `runon` on this machine. Treat it as a
**black box**: do not read, clone, or modify its source, and do not work around
anything that fails. Your job is to find out what actually happens here and
report it precisely.

**Rules**

- Run every step even if an earlier one fails. Note failures and continue.
- For anything that fails, record the **exact command** and the **exact output**.
- Never edit runon's code or patch around a problem.
- Say which steps you could not run and why (missing tmux, no second machine, etc).
- Do not guess or infer. If you did not run it, say you did not run it.
- An `ok` is not a pass on its own. Several of the bugs below reported success
  while doing nothing, so where a step says to check a *value*, check the value.

**Note what you change.** Everything lands in `~/.runon/`, plus one completion
file. List every path you created at the end.

---

## 1. Install

First find out what you have:

```bash
command -v runon && runon --version
python3 -V                      # runon needs 3.10+
pipx --version 2>/dev/null || echo "no pipx"
```

Then upgrade with whichever applies. **Check the version afterwards** — several
of these exit 0 having changed nothing:

```bash
# --force, not upgrade: `pipx upgrade` has been seen reporting "already at
# latest version" and exiting 0 against an index that had not caught up yet,
# so the || guard never fires and you test the old build.
pipx install --force runon --pip-args="--no-cache-dir"
# no pipx, and python3 is 3.10 or newer:
python3 -m pip install -U --no-cache-dir runon
# no pipx, and python3 is older than 3.10 — pip will refuse, so get a newer one:
#   sudo apt install python3.11-venv && python3.11 -m venv ~/.venvs/runon
#   ~/.venvs/runon/bin/pip install --no-cache-dir runon

runon --version
```

`--no-cache-dir` matters: PyPI's CDN has repeatedly served a stale version.

**It must be 0.14.0 or newer.** If it is older, say so and stop — everything
after this tests the wrong build. If you could not upgrade at all, say that
too; do not test whatever happened to be installed and report it as a pass.

**If your `python3` is 3.10** (Ubuntu 22.04 ships it), that is the interesting
case: before 0.14.0 `pip install runon` there failed with `No matching
distribution found for runon`, which reads as the package not existing. Record
what it does now, and what `pip list` shows:

```bash
python3 -m pip list 2>/dev/null | grep -iE 'runon|tomli'
```

On 3.10 you should see `runon` **and** `tomli`. On 3.11+ you should see
`runon` and nothing else.

## 2. First run creates a workspace and installs completion

In a **fresh terminal**, from a directory that is not a workspace:

```bash
cd /
runon list programs
```

Report the full output. Expected: a line saying it created `~/.runon/workspace`,
a line about completion being installed, and `hello-world` listed.

```bash
runon config
runon doctor
```

Paste both outputs verbatim — `doctor` is the main diagnostic.

Now `runon init`, which is what the README tells a new user to run. Before
0.14.0 this refused, because step 2 had already created the workspace:

```bash
runon init; echo "exit=$?"
```

It must **succeed** (exit 0) and must not overwrite anything.

## 3. Tab completion

This is the one most often reported broken. **Open a brand-new terminal** (not
the one above — bash caches "no completion" for a command it failed to complete
once).

Then, interactively:

- Type `runon ` and press TAB. Record what appears.
- Type `runon local run-program ` and press TAB. Record what appears.
- Type `runon host --host ` and press TAB. Record what appears.

The third one must offer **host names**, not verbs.

If nothing happens, report:

```bash
echo "$SHELL"; echo "$XDG_DATA_HOME"
ls -l ~/.local/share/bash-completion/completions/runon
dpkg -l bash-completion 2>/dev/null | tail -1
complete -p runon
```

**If zsh or fish is installed here**, test that shell too — each has its own
script and they fail differently:

```bash
command -v zsh  && runon completion zsh  --install
command -v fish && runon completion fish --install
```

Then start that shell and try the same three completions. For fish you can
check it without an interactive session:

```bash
fish -c 'complete -C "runon host --host "'      # must list host names
fish -c 'complete -C "runon local run-program "' # must list program names
```

## 4. Running locally

```bash
runon local run-program hello-world --verbose
```

## 5. Arguments reaching the program

```bash
mkdir -p ~/.runon/workspace/programs/args
cat > ~/.runon/workspace/programs/args/main.sh <<'EOF'
#!/bin/sh
# echoes what it was handed
echo "count=$# args=[$*]"
EOF
chmod +x ~/.runon/workspace/programs/args/main.sh

runon local run-program -v args 80 90
runon local run-program -v args -- --since 1h
runon local run-program -v args "one two"
```

Expected: `count=2 args=[80 90]`, then `count=2 args=[--since 1h]`, then
`count=1`. The `--` form is how you pass an argument that starts with a dash;
without it runon reads the dash as one of its own flags.

Note where `-v` is. runon's own flags go **before** the program name or after
all of its arguments, never between them — this is an argparse limitation, not
a choice, and it is worth confirming the error is clear rather than silent:

```bash
runon local run-program args -v 80 90; echo "exit=$?"
```

Expected: `error: unrecognized arguments: 80 90`, exit 2. Report it if this
ever exits 0, because that would mean arguments were dropped.

## 6. Adding a host, non-interactively

Set up ssh to this machine so there is a real target:

```bash
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
grep -qF "$(cat ~/.ssh/id_ed25519.pub)" ~/.ssh/authorized_keys 2>/dev/null \
  || cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new localhost 'echo ssh works'
```

If that last line fails, install/start sshd (`sudo apt install -y openssh-server`,
`sudo systemctl start ssh`) and say that you had to.

```bash
runon add-host self --address localhost --user "$USER" < /dev/null
runon list hosts
```

An ssh_config alias or a bare hostname is deliberately **not** accepted as an
ad-hoc target — that is typo protection. Check the refusal says what to do:

```bash
runon host --host somealias run-program hello-world < /dev/null; echo "exit=$?"
```

It must exit 2 and suggest `runon add-host somealias --address somealias`.

## 7. Copying and running on a real host

```bash
runon host --host self copy-run-program hello-world --verbose
ls -R ~/.runon/programs ~/.runon/functions
```

Then **run it a second time** and list again:

```bash
runon host --host self copy-run-program hello-world --verbose
ls ~/.runon/programs/hello-world
```

Report whether `~/.runon/programs/hello-world/hello-world` exists. It must not.

## 8. Parameters and prompts reaching the program

```bash
cat > ~/.runon/workspace/programs/hello-world/params.toml <<'EOF'
threshold = 90
EOF
cat > ~/.runon/workspace/programs/hello-world/prompts.toml <<'EOF'
[[prompt]]
key = "branch"
title = "Branch"
default = "main"
EOF
cat > ~/.runon/workspace/programs/hello-world/main.sh <<'EOF'
#!/bin/sh
# reports what it was given
echo "threshold=$RUNON_PARAM_THRESHOLD branch=$RUNON_PROMPT_BRANCH host=$RUNON_HOST"
echo "functions=$RUNON_FUNCTIONS"
ls "$RUNON_FUNCTIONS" >/dev/null && echo "functions dir is real"
EOF
chmod +x ~/.runon/workspace/programs/hello-world/main.sh

runon host --host self copy-run-program hello-world --verbose < /dev/null
```

All four values must be non-empty and `functions dir is real` must print.
**Check the values, not the `ok`** — an earlier version printed `ok` here with
every one of them empty.

Then interactively (a real terminal, no `< /dev/null`), so it asks:

```bash
runon host --host self copy-run-program hello-world --verbose
```

Answer the Branch prompt with `hotfix` and confirm it appears in the output.

## 9. Unattended, with a stored password

```bash
runon add-host self-env --address localhost --user "$USER" --password-env SELF_PASS < /dev/null
printf '%s' 'not-a-real-password' | runon add-host self-file --address localhost --user "$USER" --password-stdin
ls -l ~/.runon/secrets/
stat -c '%a %n' ~/.runon/secrets ~/.runon/secrets/self-file
```

The directory must be `700` and the file `600`.

Then check the inventory holds no secret:

```bash
grep -q 'not-a-real-password' ~/.runon/workspace/inventory.toml \
  && echo "BAD: the secret is in the inventory" \
  || echo "good: no secret in the inventory"
```

And that an inline password is refused:

```bash
printf '\n[hosts.bad]\naddress = "10.0.0.1"\npassword = "hunter2"\n' >> ~/.runon/workspace/inventory.toml
runon list hosts; echo "exit=$?"
# then remove those 4 lines again
```

A password file anyone else can read must also be refused:

```bash
chmod 644 ~/.runon/secrets/self-file
runon host --host self-file run-program hello-world < /dev/null; echo "exit=$?"
chmod 600 ~/.runon/secrets/self-file
```

It must name the mode and print the `chmod 600` that fixes it.

## 10. Destructive confirmation

```bash
cat > ~/.runon/workspace/programs/hello-world/meta.toml <<'EOF'
title = "Greeter"
description = "Says hello"
details = "The longer text, shown in the picker's preview pane."
destructive = true
confirm_message = "This is only a test."
tags = ["test"]
related = ["second"]
EOF

runon local run-program hello-world -v < /dev/null; echo "exit=$?"   # must refuse
runon local run-program hello-world -v --yes                          # must run
```

Then interactively, without `--yes`, and answer `n`. It must cancel **and exit
130** — a script has to be able to tell "you said no" from "it worked".

## 11. The picker

Interactively, in a real terminal:

```bash
runon new-program second < /dev/null
runon local run-program
```

Report whether a full-screen picker appears with category tabs. Try the arrow
keys, then type `hello` to filter down to it.

The preview describes the **highlighted** row, so with `hello-world`
highlighted it should show three lines from the `meta.toml` in step 10:

```
 › hello-world [destructive]  — Says hello
  The longer text, shown in the picker's preview pane.
  tags: test
  see also: second
```

If it looks broken or garbled, **describe exactly what you see** and paste a
copy of the screen. Also try:

```bash
RUNON_PLAIN=1 runon local run-program
```

which should give a plain numbered menu instead. Pressing Escape in the picker,
or Enter on the blank prompt in the menu, must exit **130**.

## 12. Watch mode, if tmux is present

```bash
command -v tmux && runon host --host self --watch copy-run-program hello-world --yes
```

Report whether panes open, and whether each pane is titled with its host.

```bash
runon host --host self --watch --headless copy-run-program hello-world --yes
```

which must run without opening tmux at all.

Then point `--watch` at a machine that cannot be logged in to. It must **not**
open a pane for it, and must **not** exit 0:

```bash
runon host --host 192.0.2.1 --watch run-program hello-world --yes < /dev/null; echo "exit=$?"
```

Expected: a "could not log in" message and a non-zero exit. Before 0.14.0 this
printed a tmux session name and exited 0 with every pane dead.

## 13. Non-interactive safety

With stdin closed, these must **fail with a clear message**, not hang and not
exit 0 having done nothing.

The group case only reaches the guard if groups exist, so make some first —
including the single-group case, which used to be auto-selected and run:

```bash
cat >> ~/.runon/workspace/inventory.toml <<'EOF'

[groups.one]
hosts = ["self"]
EOF

runon host  run-program hello-world --dry-run < /dev/null; echo "exit=$?"
runon group run-program hello-world --dry-run < /dev/null; echo "exit=$?"   # one group
```

Both must refuse and exit 2. **The single-group case matters**: with nobody
watching, runon must not choose the only group for you, because tomorrow there
are two and the same cron line means something else.

Then add a second group and check it still refuses, naming both:

```bash
cat >> ~/.runon/workspace/inventory.toml <<'EOF'

[groups.two]
hosts = ["self", "self-env"]
EOF

runon group run-program hello-world --dry-run < /dev/null; echo "exit=$?"
```

`run-layout` has the same guard, and must name **its own** flag:

```bash
runon local run-layout < /dev/null; echo "exit=$?"
```

It must say `--layout`, not `--program`.

Remove both group blocks afterwards.

## 14. The things that used to fail quietly

Each of these was a real failure on somebody's machine. They are grouped
because they are quick and none of them needs a second host.

**A long home directory must not break every remote command.** The control
socket has a hard length limit, and the path used to be built under
`RUNON_HOME` with no bound:

`RUNON_HOME` moves the workspace as well as the sockets, so point runon back at
the real one with `-C` — otherwise you are only testing an empty workspace:

```bash
W=~/.runon/workspace
export LONGHOME=/tmp/runon-acceptance/CORP.EXAMPLE.COM/firstname.lastname/.runon
mkdir -p "$LONGHOME"

RUNON_HOME="$LONGHOME" runon -C "$W" --inventory "$W/inventory.toml" \
  host --host self run-program hello-world -v < /dev/null; echo "exit=$?"

RUNON_HOME=/proc/nowhere/runon runon -C "$W" --inventory "$W/inventory.toml" \
  host --host self run-program hello-world -v < /dev/null; echo "exit=$?"
```

Both must **succeed** (exit 0). Previously the first said `ControlPath too long`
and the second gave a bare errno, and every remote verb failed. Connection reuse
falls back to a shorter directory — `/run/user/<uid>/runon` or
`/tmp/runon-<uid>` — rather than failing the command.

**A program with Windows line endings must say so.** A workspace is meant to be
committed and shared, so this reaches people who did nothing wrong:

```bash
mkdir -p ~/.runon/workspace/programs/crlf
printf '#!/bin/sh\r\necho hi\r\n' > ~/.runon/workspace/programs/crlf/main.sh
chmod +x ~/.runon/workspace/programs/crlf/main.sh
runon local run-program crlf -v < /dev/null; echo "exit=$?"
```

It must name CRLF and print the `sed` line that fixes it — not
`./main.sh: not found`.

**A bad `--persist` must be caught before connecting**, not per host as
`Bad ControlPersist argument` with exit 255:

```bash
runon host --host self --persist banana run-program hello-world < /dev/null; echo "exit=$?"
runon host --host self --persist 1h30m run-program hello-world < /dev/null; echo "exit=$?"
```

First must exit 2 with an explanation; second must work.

**`--timeout` must exist and be honoured.** There was no way to run anything
longer than an hour:

```bash
mkdir -p ~/.runon/workspace/programs/slow
printf '#!/bin/sh\nsleep 8\necho finished\n' > ~/.runon/workspace/programs/slow/main.sh
chmod +x ~/.runon/workspace/programs/slow/main.sh
runon local run-program slow --timeout 2 -v; echo "exit=$?"     # must be cut off
runon local run-program slow --timeout 30 -v; echo "exit=$?"    # must finish
```

**`doctor` must not hang on a wedged ssh-agent.** It is the command you run
when something is already wrong:

```bash
mkdir -p /tmp/runon-slowbin
printf '#!/bin/sh\nsleep 300\n' > /tmp/runon-slowbin/ssh-add
chmod +x /tmp/runon-slowbin/ssh-add
time PATH=/tmp/runon-slowbin:$PATH runon doctor
```

It must return in seconds and say the agent did not answer.

**An IPv6 address must work for copying, not just running.** `scp` needs
brackets and `ssh` must not have them:

```bash
ssh -o StrictHostKeyChecking=accept-new ::1 'echo ipv6 ssh works' 2>&1 | tail -1
runon host --host ::1 copy-run-program hello-world -v < /dev/null; echo "exit=$?"
```

If the first line fails, this machine has no IPv6 sshd — say so and skip. If it
works, the second must too. Previously it failed with
`cp: cannot create directory '::1:~/.runon/programs/'`.

## 15. A remote account whose login shell is not Bourne — optional

Skip this if you cannot use `sudo`; say that you skipped it.

ssh runs runon's command in whatever login shell the target account has.
Against `tcsh` this used to report `ok` while delivering **nothing** — no
parameters, no prompts, no functions library. FreeBSD gives root a tcsh by
default.

```bash
sudo apt-get install -y -qq tcsh
sudo useradd -m -s /usr/bin/tcsh cshuser
sudo mkdir -p /home/cshuser/.ssh
cat ~/.ssh/id_ed25519.pub | sudo tee /home/cshuser/.ssh/authorized_keys >/dev/null
sudo chown -R cshuser /home/cshuser/.ssh
sudo chmod 700 /home/cshuser/.ssh && sudo chmod 600 /home/cshuser/.ssh/authorized_keys

runon add-host cshbox --address localhost --user cshuser < /dev/null
runon host --host cshbox copy-run-program hello-world --verbose < /dev/null
```

Every value must be non-empty and `functions dir is real` must print, exactly
as in step 8. An `ok` with empty values is the bug.

Clean up with `sudo userdel -r cshuser` and remove the `cshbox` host.

---

## Report back

1. `runon --version`, `python3 -V`, and `runon doctor` output.
2. A table of every numbered step: pass / fail / not run, one line each.
3. For each failure: the exact command, the exact output, and nothing else —
   no diagnosis unless you actually verified the cause.
4. Every path you created, so it can be cleaned up. This run adds at least
   `/tmp/runon-acceptance/`, `/tmp/runon-slowbin/`, and possibly
   `/tmp/runon-$(id -u)/` alongside the usual `~/.runon/`.
5. Anything that worked but felt wrong, confusing, or slow.
