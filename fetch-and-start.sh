#!/bin/env bash

set -euo pipefail
IFS=$'\n\t'

export RAY_AUTH_MODE="${RAY_AUTH_MODE:-token}"
if [[ "$RAY_AUTH_MODE" = token && -z "${RAY_AUTH_TOKEN:-}" && -z "${RAY_AUTH_TOKEN_PATH:-}" ]]; then
	# OpenShift may use an arbitrary UID without a writable home directory.
	export RAY_AUTH_TOKEN_PATH="$(mktemp -d)/auth_token"
	(umask 077; python -c 'import secrets; print(secrets.token_hex(32))' > "$RAY_AUTH_TOKEN_PATH")
fi

export RCLONE_S3_ENDPOINT=https://a3s.fi
cd "$DATABASE_DIR"
rclone copyto --progress :s3,env_auth:apitofsim-data/database.ase.sqlite.db .
rclone copyto --progress :s3,env_auth:apitofsim-data/database.duckdb .

export DATABASE="$DATABASE_DIR/database.duckdb"
export RESULTS="$DATABASE_DIR/results"
# Only ever use 1 worker since it is stateful
exec uvicorn vms:app --host 0.0.0.0 --port 8080 --workers 1
