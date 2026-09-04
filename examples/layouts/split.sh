#!/usr/bin/env sh
# Opens a tmux session with the panes you always want together.
set -eu

SESSION="${1:-work}"
command -v tmux >/dev/null || { echo "tmux is not installed"; exit 1; }

tmux new-session -d -s "$SESSION" 2>/dev/null || true
tmux split-window -h -t "$SESSION" 2>/dev/null || true
echo "attach with: tmux attach -t $SESSION"
