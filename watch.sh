#!/usr/bin/env bash
# Run the CSS and JS watchers side by side.
#
# Both children inherit this script's stdout and stderr, so their output
# interleaves in arrival order -- no log file, and no `tail -f` (which would
# otherwise sit on a deleted inode). As soon as either watcher exits, the other
# is taken down and this script exits with the same status.

set -m # each job gets its own process group, so one signal kills a whole tree

tmpdir="$(mktemp -d)"
pids=()
cleanup() {
    trap - INT TERM EXIT
    kill -- "${pids[@]/#/-}" 2>/dev/null
    wait 2>/dev/null
    rm -rf "$tmpdir"
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

# Both watchers quit on stdin EOF, so give them a FIFO that we hold open
# ourselves. They then behave the same whether or not this script has a
# terminal, and Ctrl-C is handled below rather than by the tools.
mkfifo "$tmpdir/stdin"
exec 9<>"$tmpdir/stdin"

for task in watch-css watch-js; do
    npm run "$task" <"$tmpdir/stdin" &
    pids+=($!)
done

status=0
wait -n || status=$?
exit "$status"
