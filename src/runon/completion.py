# ruff: noqa: E501 - these are shell scripts held as strings; a wrapped line
# here is a broken completion, not a tidier one.
"""Shell completion, without a dependency to provide it.

argcomplete would do this generically, but it is a runtime dependency for a
package that advertises having none. These scripts are static: they know the
scopes and verbs, which are the part that is tedious to type, and they leave
program and host names to the tool itself.
"""

from __future__ import annotations

import os
from pathlib import Path

SCOPES = "local host group list init new-program doctor completion config add-host"
REMOTE_VERBS = "copy copy-program run-program copy-run-program"

BASH = f"""# runon bash completion.  Install with:
#   runon completion bash > /usr/local/etc/bash_completion.d/runon
_runon() {{
    local cur prev
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    case "$prev" in
        runon)      COMPREPLY=($(compgen -W "{SCOPES}" -- "$cur")); return;;
        host|group) COMPREPLY=($(compgen -W "{REMOTE_VERBS}" -- "$cur")); return;;
        local)      COMPREPLY=($(compgen -W "run-program run-layout" -- "$cur")); return;;
        list)       COMPREPLY=($(compgen -W "programs hosts groups layouts" -- "$cur")); return;;
        completion) COMPREPLY=($(compgen -W "bash zsh fish" -- "$cur")); return;;
        run-program|copy-program|copy-run-program|--program|-p)
                    COMPREPLY=($(compgen -W "$(runon list programs 2>/dev/null | awk '{{print $1}}')" -- "$cur")); return;;
        run-layout|--layout|-l)
                    COMPREPLY=($(compgen -W "$(runon list layouts 2>/dev/null | awk '{{print $1}}')" -- "$cur")); return;;
        --group|-g)   COMPREPLY=($(compgen -W "$(runon list groups 2>/dev/null | awk '{{print $1}}')" -- "$cur")); return;;
        --host|-H)    COMPREPLY=($(compgen -W "$(runon list hosts 2>/dev/null | awk '{{print $1}}')" -- "$cur")); return;;
    esac
    COMPREPLY=($(compgen -W "--program --host --group --parallel --watch --ask-password --persist --yes --dry-run --verbose --help" -- "$cur"))
}}
complete -F _runon runon
"""

ZSH = f"""#compdef runon
# runon zsh completion.  Install with:
#   runon completion zsh > "${{fpath[1]}}/_runon"   # then: compinit
_runon() {{
    local -a scopes verbs
    scopes=({SCOPES})
    verbs=({REMOTE_VERBS})

    case "${{words[2]}}" in
        host|group)
            if (( CURRENT == 3 )); then compadd -- $verbs; return; fi ;;
        local)
            if (( CURRENT == 3 )); then compadd -- run-program run-layout; return; fi ;;
        list)
            if (( CURRENT == 3 )); then compadd -- programs hosts groups layouts; return; fi ;;
        completion)
            if (( CURRENT == 3 )); then compadd -- bash zsh fish; return; fi ;;
    esac

    if (( CURRENT == 2 )); then compadd -- $scopes; return; fi

    case "${{words[CURRENT-1]}}" in
        run-program|copy-program|copy-run-program|--program|-p)
            compadd -- ${{(f)"$(runon list programs 2>/dev/null | awk '{{print $1}}')"}}; return;;
        run-layout|--layout|-l)
            compadd -- ${{(f)"$(runon list layouts 2>/dev/null | awk '{{print $1}}')"}}; return;;
        --group|-g)   compadd -- ${{(f)"$(runon list groups 2>/dev/null | awk '{{print $1}}')"}}; return;;
        --host|-H)    compadd -- ${{(f)"$(runon list hosts 2>/dev/null | awk '{{print $1}}')"}}; return;;
    esac
    compadd -- --program --host --group --parallel --watch --ask-password --persist --yes --dry-run --verbose --help
}}
_runon "$@"
"""

FISH = f"""# runon fish completion.  Install with:
#   runon completion fish > ~/.config/fish/completions/runon.fish
complete -c runon -f
complete -c runon -n __fish_use_subcommand -a "{SCOPES}"
complete -c runon -n "__fish_seen_subcommand_from host group" -a "{REMOTE_VERBS}"
complete -c runon -n "__fish_seen_subcommand_from local" -a "run-program run-layout"
complete -c runon -n "__fish_seen_subcommand_from list" -a "programs hosts groups layouts"
complete -c runon -n "__fish_seen_subcommand_from completion" -a "bash zsh fish"
complete -c runon -n "__fish_seen_subcommand_from run-program copy-program copy-run-program" -a "(runon list programs 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -n "__fish_seen_subcommand_from run-layout" -a "(runon list layouts 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -l program -s p -a "(runon list programs 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -l layout -s l -a "(runon list layouts 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -l host -s H -a "(runon list hosts 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -l group -s g -a "(runon list groups 2>/dev/null | awk '{{print \\$1}}')"
complete -c runon -l watch -d "run in tmux, one pane per host"
complete -c runon -l ask-password -d "prompt for an SSH password"
complete -c runon -l dry-run -d "show what would happen"
"""

SCRIPTS = {"bash": BASH, "zsh": ZSH, "fish": FISH}


def script(shell: str) -> str:
    return SCRIPTS[shell]


def default_shell() -> str | None:
    """The shell that launched us, from $SHELL.

    $SHELL rather than the parent process: it is the login shell, which is the
    one whose startup files a completion has to be installed into.
    """
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in SCRIPTS else None


def install_path(shell: str, *, user_only: bool = False) -> Path:
    """Where this shell looks for completions, per shell.

    bash and fish both read a user directory with no configuration at all.
    zsh reads $fpath, which is why its answer needs a line in .zshrc — there is
    no user directory zsh searches by default.

    `user_only` keeps everything under the home directory. The automatic
    first-run install uses it: writing into a shared system directory is a
    reasonable thing to do when someone asked for it, and not something a
    side effect of `runon list programs` should ever do.
    """
    home = Path.home()
    if shell == "bash":
        xdg = os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"
        return Path(xdg) / "bash-completion" / "completions" / "runon"
    if shell == "zsh":
        site = None if user_only else _writable_zsh_site_dir()
        return (site / "_runon") if site else (home / ".zsh" / "completions" / "_runon")
    return home / ".config" / "fish" / "completions" / "runon.fish"


#: Directories already on zsh's default $fpath. A completion dropped in one of
#: these needs no line in anybody's .zshrc.
ZSH_SITE_DIRS = (
    "/opt/homebrew/share/zsh/site-functions",
    "/usr/local/share/zsh/site-functions",
    "/usr/share/zsh/site-functions",
    "/usr/share/zsh/vendor-completions",
)


def _writable_zsh_site_dir() -> Path | None:
    for candidate in ZSH_SITE_DIRS:
        path = Path(candidate)
        if path.is_dir() and os.access(path, os.W_OK):
            return path
    return None


def install(shell: str, *, user_only: bool = False) -> tuple[Path, str]:
    """Writes the completion where `shell` will find it.

    Returns the path and whatever still has to be done by hand — empty when
    nothing does.
    """
    path = install_path(shell, user_only=user_only)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script(shell), encoding="utf-8")

    if shell == "zsh":
        if str(path.parent) in ZSH_SITE_DIRS:
            # Already on zsh's default fpath, so there is nothing to configure.
            return path, "Start a new shell to pick it up."
        return path, (
            f"Add this to ~/.zshrc, above `compinit`:\n"
            f"    fpath=({path.parent} $fpath)\n"
            "Then start a new shell, or run: compinit"
        )
    if shell == "bash":
        return path, "Start a new shell to pick it up."
    return path, ""
