# ruff: noqa: E501 - these are shell scripts held as strings; a wrapped line
# here is a broken completion, not a tidier one.
"""Shell completion, without a dependency to provide it.

argcomplete would do this generically, but it is a runtime dependency for a
package that advertises having none. These scripts are static: they know the
scopes and verbs, which are the part that is tedious to type, and they leave
program and host names to the tool itself.
"""

from __future__ import annotations

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
    COMPREPLY=($(compgen -W "--program --host --group --parallel --watch --ask-password --persist --dry-run --verbose --help" -- "$cur"))
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
    compadd -- --program --host --group --parallel --watch --ask-password --persist --dry-run --verbose --help
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
