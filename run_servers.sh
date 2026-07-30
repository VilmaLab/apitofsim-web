#!/bin/bash
set -euo pipefail

SESSION=apitofsim-web
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PREFIX="$ROOT/cenv"
RAY_TMP=/tmp/raytmp
PORTS=(6379 5000)

usage() {
	echo "usage: ${0##*/} [attach|restart]" >&2
	exit 2
}

ACTION=""
case "${1-}" in
	"") ;;
	attach|restart) ACTION="$1" ;;
	*) usage ;;
esac

if [ ! -x "$ENV_PREFIX/bin/ray" ]; then
	echo "No micromamba env at $ENV_PREFIX (see README: micromamba create -f env.yaml -p ./cenv)" >&2
	exit 1
fi

# PIDs listening on a port, if any.
port_pids() {
	ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
}

port_in_use() {
	[ -n "$(port_pids "$1")" ]
}

busy_ports() {
	local p
	for p in "${PORTS[@]}"; do
		port_in_use "$p" && echo "$p"
	done
	return 0
}

# Wait up to $1 seconds for all PORTS to be free. Returns non-zero on timeout.
wait_ports_free() {
	local deadline=$((SECONDS + $1)) busy
	while [ "$SECONDS" -lt "$deadline" ]; do
		busy="$(busy_ports)"
		[ -z "$busy" ] && return 0
		sleep 1
	done
	return 1
}

teardown() {
	echo "Stopping ray..."
	micromamba run -p "$ENV_PREFIX" ray stop --grace-period 15 >/dev/null 2>&1 || true

	if tmux has-session -t "$SESSION" 2>/dev/null; then
		echo "Killing tmux session '$SESSION'..."
		tmux kill-session -t "$SESSION" || true
	fi

	if wait_ports_free 20; then
		echo "Ports free."
		return 0
	fi

	echo "Ports ${PORTS[*]} still busy; forcing." >&2
	micromamba run -p "$ENV_PREFIX" ray stop --force >/dev/null 2>&1 || true

	local sig pid port
	for sig in TERM KILL; do
		for port in "${PORTS[@]}"; do
			for pid in $(port_pids "$port"); do
				echo "  SIG$sig -> pid $pid (port $port)"
				kill -"$sig" "$pid" 2>/dev/null || true
			done
		done
		wait_ports_free 10 && { echo "Ports free."; return 0; }
	done

	echo "Giving up: still in use on port(s) $(busy_ports | tr '\n' ' ')" >&2
	return 1
}

if tmux has-session -t "$SESSION" 2>/dev/null; then
	if [ -z "$ACTION" ]; then
		if [ -t 0 ]; then
			echo "Session '$SESSION' already exists."
			read -r -p "[a]ttach, [r]estart, [q]uit? " reply
			case "$reply" in
				a|A|attach|"") ACTION=attach ;;
				r|R|restart) ACTION=restart ;;
				*) exit 0 ;;
			esac
		else
			echo "Session '$SESSION' already exists; pass 'attach' or 'restart'." >&2
			exit 1
		fi
	fi

	if [ "$ACTION" = attach ]; then
		exec tmux attach-session -t "$SESSION"
	fi
	teardown
elif [ -n "$(busy_ports)" ]; then
	# No session, but leftovers from a previous run are squatting on the ports.
	echo "No session, but port(s) $(busy_ports | tr '\n' ' ')in use; cleaning up."
	teardown
fi

mkdir -p "$RAY_TMP"

if [ -z "${DATABASE-}" ]; then
	echo "Warning: DATABASE is not set; requests will fail with KeyError: 'DATABASE'." >&2
fi

# tmux panes inherit the tmux *server's* environment, which is stale (or absent)
# when a server is already running, so replay our own environment into them.
ENV_DUMP="$(export -p | grep -vE '^declare -x (PWD|OLDPWD|SHLVL|_|TMUX|TMUX_PANE)=')"

RAY_CMD="micromamba run -p '$ENV_PREFIX' ray start \
--head \
--object-store-memory 512000000 \
--temp-dir $RAY_TMP \
--num-cpus 1 \
--port 6379 \
--include-dashboard false \
--block"

WEB_CMD="
echo 'Waiting for ray on 127.0.0.1:6379...'
for i in \$(seq 1 120); do
	if (exec 3<>/dev/tcp/127.0.0.1/6379) 2>/dev/null; then
		exec 3>&-
		echo 'Ray is up.'
		RAY_ADDRESS="localhost:6379" exec micromamba run -p '$ENV_PREFIX' quart --debug --app vms run
	fi
	sleep 1
done
echo 'Timed out waiting for ray after 120s.' >&2
exec bash"

tmux new-session -d -s "$SESSION" -n servers -c "$ROOT" \
	bash -c "$ENV_DUMP
$RAY_CMD; echo; echo '[ray exited]'; exec bash"

tmux split-window -t "$SESSION:servers" -h -c "$ROOT" \
	bash -c "$ENV_DUMP
$WEB_CMD"

tmux select-layout -t "$SESSION:servers" even-vertical

exec tmux attach-session -t "$SESSION"
