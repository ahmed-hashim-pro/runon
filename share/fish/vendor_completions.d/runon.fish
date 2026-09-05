# runon fish completion.  Install with:
#   runon completion fish > ~/.config/fish/completions/runon.fish
complete -c runon -f
complete -c runon -n __fish_use_subcommand -a "local host group list init new-program doctor completion config add-host"
complete -c runon -n "__fish_seen_subcommand_from host group" -a "copy copy-program run-program copy-run-program"
complete -c runon -n "__fish_seen_subcommand_from local" -a "run-program run-layout"
complete -c runon -n "__fish_seen_subcommand_from list" -a "programs hosts groups layouts"
complete -c runon -n "__fish_seen_subcommand_from completion" -a "bash zsh fish"
complete -c runon -n "__fish_seen_subcommand_from run-program copy-program copy-run-program" -a "(runon list programs 2>/dev/null | awk '{print \$1}')"
complete -c runon -n "__fish_seen_subcommand_from run-layout" -a "(runon list layouts 2>/dev/null | awk '{print \$1}')"
complete -c runon -l program -s p -a "(runon list programs 2>/dev/null | awk '{print \$1}')"
complete -c runon -l layout -s l -a "(runon list layouts 2>/dev/null | awk '{print \$1}')"
complete -c runon -l host -s H -a "(runon list hosts 2>/dev/null | awk '{print \$1}')"
complete -c runon -l group -s g -a "(runon list groups 2>/dev/null | awk '{print \$1}')"
complete -c runon -l watch -d "run in tmux, one pane per host"
complete -c runon -l ask-password -d "prompt for an SSH password"
complete -c runon -l dry-run -d "show what would happen"
