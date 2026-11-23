#!/usr/bin/env bash
set -euo pipefail

export RAY_AUTH_MODE="${RAY_AUTH_MODE:-token}"
export RESULTS_DIR="${RESULTS_DIR:-/resultdbs}"
export RESULTS_MAX_SIZE="${RESULTS_MAX_SIZE:-20G}"

database_dir=$(realpath -m -- "${DATABASE_DIR:-/database}")
RESULTS_DIR=$(realpath -m -- "$RESULTS_DIR")
if [[ "$RESULTS_DIR" == / || "$RESULTS_DIR" == "$database_dir" ]]; then
    printf 'RESULTS_DIR must be a dedicated directory, separate from DATABASE_DIR.\n' >&2
    exit 1
fi
mkdir -p -- "$database_dir" "$RESULTS_DIR"

declare -A services=()
token_dir=
stop_services() {
    (( $# )) || return 0
    local pid deadline=$((SECONDS + 4))
    local -a running=("$@")
    kill -TERM "$@" 2>/dev/null || true
    while (( ${#running[@]} && SECONDS < deadline )); do
        sleep 0.2
        running=()
        for pid in "$@"; do
            if kill -0 "$pid" 2>/dev/null; then
                running+=("$pid")
            fi
        done
    done
    if (( ${#running[@]} )); then
        kill -KILL "${running[@]}" 2>/dev/null || true
    fi
    wait "$@" 2>/dev/null || true
}

cleanup() {
    # Let the web process disconnect from Ray before stopping the head.
    if [[ -n "${services[Web]:-}" ]]; then
        stop_services "${services[Web]}"
        unset 'services[Web]'
    fi
    stop_services "${services[@]}"
    if [[ -n "$token_dir" ]]; then
        rm -f -- "$token_dir/auth_token"
        rmdir -- "$token_dir"
    fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# Fetch must succeed before any simulation service starts.
export RCLONE_S3_ENDPOINT=https://a3s.fi
for filename in database.ase.sqlite.db database.duckdb; do
    rclone copyto --progress ":s3,env_auth:apitofsim-data/$filename" "$database_dir/$filename" &
    services[Fetch]=$!
    wait "${services[Fetch]}"
    unset 'services[Fetch]'
done
export DATABASE="$database_dir/database.duckdb"

# Use the local head by default. Explicit external addresses still work.
if [[ "${RAY_ADDRESS:-local}" == local || "${RAY_ADDRESS:-}" == auto ]]; then
    if [[ "${RAY_AUTH_MODE,,}" == token && -z "${RAY_AUTH_TOKEN:-}" && -z "${RAY_AUTH_TOKEN_PATH:-}" ]]; then
        # Rahti can assign a UID with no writable home.
        token_dir=$(mktemp -d)
        export RAY_AUTH_TOKEN_PATH="$token_dir/auth_token"
        (umask 077; python -c 'import secrets; print(secrets.token_hex(32))' > "$RAY_AUTH_TOKEN_PATH")
    fi
    ray start --head --port=6379 --block --disable-usage-stats &
    services[Ray]=$!
    export RAY_ADDRESS=auto
    deadline=$((SECONDS + 120))
    # The Ray CLI can take more than five seconds on a CPU-limited pod.
    until timeout --verbose 20s ray status; do
        if ! kill -0 "${services[Ray]}" 2>/dev/null; then
            status=0
            wait "${services[Ray]}" || status=$?
            printf 'Ray exited before becoming ready (status %d).\n' "$status" >&2
            unset 'services[Ray]'
            exit "$(( status == 0 ? 1 : status ))"
        fi
        if (( SECONDS >= deadline )); then
            printf 'Ray did not become ready within 120 seconds.\n' >&2
            exit 1
        fi
        sleep 1
    done
fi

dirroof --size "$RESULTS_MAX_SIZE" "$RESULTS_DIR" &
services[Roof]=$!
# The web process owns the in-memory job queue, so run one worker.
uvicorn vms:app --host 0.0.0.0 --port 8080 --workers 1 &
services[Web]=$!

# Include children that exited before the other services finished starting.
while true; do
    for service in "${!services[@]}"; do
        pid=${services[$service]}
        if ! kill -0 "$pid" 2>/dev/null; then
            status=0
            wait "$pid" || status=$?
            printf '%s exited unexpectedly (status %d).\n' "$service" "$status" >&2
            unset 'services[$service]'
            exit "$(( status == 0 ? 1 : status ))"
        fi
    done
    sleep 1
done
