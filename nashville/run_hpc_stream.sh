#!/bin/bash
# Runs the HPC supervisor inside a detached tmux session and streams its
# terminal live to asciinema.org. The run survives a lost SSH/VS Code
# connection, and the dashboard can be watched from any browser.
#
# One-time setup: link the asciinema CLI to your account with `asciinema auth`
# (required for remote streaming).
#
# Usage: bash nashville/run_hpc_stream.sh [command...]
#   command defaults to `bash nashville/run_hpc.sh`
#
# Environment:
#   SIM_TMUX_SESSION      tmux session name (default: sim)
#   ASCIINEMA_STREAM_ID   reuse a stream from your asciinema.org account so the
#                         URL stays the same across runs (default: new stream)
#   ASCIINEMA_VISIBILITY  public | unlisted | private (default: private)
#   ASCIINEMA_WINDOW_SIZE terminal size the run and stream use, as COLSxROWS
#                         (default: 120x60). The web player can't scroll back,
#                         so this must fit the whole output. It is fixed on
#                         purpose: tmux would otherwise shrink the stream to the
#                         smallest attached terminal.

set -euo pipefail

cd "$(dirname "$0")/.."

SESSION="${SIM_TMUX_SESSION:-sim}"
SERVER_URL="${ASCIINEMA_SERVER_URL:-https://asciinema.org}"
[ $# -gt 0 ] || set -- bash nashville/run_hpc.sh

if ! command -v asciinema >/dev/null; then
    echo "asciinema not found; install asciinema 3.x into ~/.local/bin" >&2
    exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' already exists." >&2
    echo "  still running?  tmux attach -t $SESSION" >&2
    echo "  finished?       tmux kill-session -t $SESSION" >&2
    exit 1
fi

stream=(
    asciinema stream
    --server-url "$SERVER_URL"
    --visibility "${ASCIINEMA_VISIBILITY:-private}"
    --title "Nashville run $(date '+%Y-%m-%d %H:%M')"
    --window-size "${ASCIINEMA_WINDOW_SIZE:-120x60}"
    --return
    --command "$(printf '%q ' "$@")"
    --remote ${ASCIINEMA_STREAM_ID:+"$ASCIINEMA_STREAM_ID"}
)

# remain-on-exit keeps the final dashboard (or an early error) on screen after
# the command exits. It is chained into the same tmux call so it is applied
# before tmux can notice a command that fails immediately.
tmux new-session -d -s "$SESSION" -x 200 -y 50 -c "$PWD" "$(printf '%q ' "${stream[@]}")" \; \
    set-window-option -t "$SESSION" remain-on-exit on

# asciinema prints "::: Live streaming at <url>" once connected. If it exits
# before that (e.g. not authenticated), show its message and remove the
# session so it doesn't block the next attempt.
url=""
for _ in $(seq 30); do
    dead=$(tmux list-panes -t "$SESSION" -F '#{pane_dead}')
    pane=$(tmux capture-pane -p -t "$SESSION")
    url=$(grep -oE "${SERVER_URL}/[^[:space:]]+" <<<"$pane" | head -n 1 || true)
    [ -n "$url" ] && break
    if [ "$dead" = "1" ]; then
        grep -v -e '^[[:space:]]*$' -e '^Pane is dead' <<<"$pane" >&2 || true
        tmux kill-session -t "$SESSION"
        exit 1
    fi
    sleep 0.5
done

if [ -n "$url" ]; then
    echo "Streaming at: $url"
else
    echo "No stream URL yet; attach to see what asciinema reports." >&2
fi
echo "Detach with Ctrl-b d; reattach later with: tmux attach -t $SESSION"

if [ -t 0 ] && [ -z "${TMUX:-}" ]; then
    exec tmux attach -t "$SESSION"
fi
