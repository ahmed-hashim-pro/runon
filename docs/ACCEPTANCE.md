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

**Note what you change.** Everything lands in `~/.runon/`, plus one completion
file. List every path you created at the end.

---

## 1. Install

First find out what you have, and whether the system python can even run it:

```bash
command -v runon && runon --version
python3 -V                      # runon needs 3.11+
pipx --version 2>/dev/null || echo "no pipx"
```

Then upgrade with whichever applies. **Check the version afterwards** — several
of these exit 0 having changed nothing:

```bash
# --force, not upgrade: `pipx upgrade` has been seen reporting "already at
# latest version" and exiting 0 against an index that had not caught up yet,
# so the || guard never fires and you test the old build.
pipx install --force runon --pip-args="--no-cache-dir"
# no pipx, and python3 is 3.11 or newer:
python3 -m pip install -U --no-cache-dir runon
# no pipx, and python3 is older than 3.11 — pip will refuse, so get a newer one:
#   sudo apt install python3.11-venv && python3.11 -m venv ~/.venvs/runon
#   ~/.venvs/runon/bin/pip install --no-cache-dir runon

runon --version
```

`--no-cache-dir` matters: PyPI's CDN has repeatedly served a stale version.

**It must be 0.13.2 or newer.** If it is older, say so and stop — everything
after this tests the wrong build. If you could not upgrade at all, say that
too; do not test whatever happened to be installed and report it as a pass.

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

## 3. Tab completion

This is the one previously reported broken. **Open a brand-new terminal** (not
the one above — bash caches "no completion" for a command it failed to complete
once).

Then, interactively:

- Type `runon ` and press TAB. Record what appears.
- Type `runon local run-program ` and press TAB. Record what appears.

If nothing happens, report:

```bash
echo "$SHELL"; echo "$XDG_DATA_HOME"
ls -l ~/.local/share/bash-completion/completions/runon
dpkg -l bash-completion 2>/dev/null | tail -1
complete -p runon
```

## 4. Running locally

```bash
runon local run-program hello-world --verbose
```

## 5. Adding a host, non-interactively

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

## 6. Copying and running on a real host

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

## 7. Parameters and prompts reaching the program

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

Then interactively (a real terminal, no `< /dev/null`), so it asks:

```bash
runon host --host self copy-run-program hello-world --verbose
```

Answer the Branch prompt with `hotfix` and confirm it appears in the output.

## 8. Unattended, with a stored password

```bash
runon add-host self-env --address localhost --user "$USER" --password-env SELF_PASS < /dev/null
printf '%s' 'not-a-real-password' | runon add-host self-file --address localhost --user "$USER" --password-stdin
ls -l ~/.runon/secrets/
stat -c '%a %n' ~/.runon/secrets ~/.runon/secrets/self-file
```

The directory must be `700` and the file `600`.

Then check the inventory holds no secret:

```bash
grep -c 'not-a-real-password' ~/.runon/workspace/inventory.toml || echo "good: no secret in the inventory"
```

And that an inline password is refused:

```bash
printf '\n[hosts.bad]\naddress = "10.0.0.1"\npassword = "hunter2"\n' >> ~/.runon/workspace/inventory.toml
runon list hosts; echo "exit=$?"
# then remove those 4 lines again
```

## 9. Destructive confirmation

```bash
cat > ~/.runon/workspace/programs/hello-world/meta.toml <<'EOF'
title = "Greeter"
description = "Says hello"
destructive = true
confirm_message = "This is only a test."
EOF

runon local run-program hello-world -v < /dev/null; echo "exit=$?"   # must refuse
runon local run-program hello-world -v --yes                          # must run
```

Then interactively, without `--yes`, and answer `n`. It must cancel **and exit
130** — a script has to be able to tell "you said no" from "it worked".

## 10. The picker

Interactively, in a real terminal:

```bash
runon new-program second < /dev/null
runon local run-program
```

Report whether a full-screen picker appears with category tabs. Try the arrow
keys, type a few letters to filter, then Enter. If it looks broken or garbled,
**describe exactly what you see** and paste a copy of the screen. Also try:

```bash
RUNON_PLAIN=1 runon local run-program
```

which should give a plain numbered menu instead.

## 11. Watch mode, if tmux is present

```bash
command -v tmux && runon host --host self --watch copy-run-program hello-world --yes
```

Report whether panes open. Then:

```bash
runon host --host self --watch --headless copy-run-program hello-world --yes
```

which must run without opening tmux at all.

## 12. Non-interactive safety

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

Remove both group blocks afterwards.

---

## Report back

1. `runon --version` and `runon doctor` output.
2. A table of every numbered step: pass / fail / not run, one line each.
3. For each failure: the exact command, the exact output, and nothing else —
   no diagnosis unless you actually verified the cause.
4. Every path you created, so it can be cleaned up.
5. Anything that worked but felt wrong, confusing, or slow.
