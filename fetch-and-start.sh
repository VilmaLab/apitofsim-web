#!/bin/env bash

set -euo pipefail
IFS=$'\n\t'

export RCLONE_S3_ENDPOINT=https://a3s.fi
cd "$DATABASE_DIR"
rclone copyto --progress :s3,env_auth:apitofsim-data/database.ase.sqlite.db .
rclone copyto --progress :s3,env_auth:apitofsim-data/database.duckdb .

export DATABASE="$DATABASE_DIR/database.duckdb"
export RESULTS="$DATABASE_DIR/results"
# Only ever use 1 worker since it is stateful
exec uvicorn vms:app --host 0.0.0.0 --port 8080 --workers 1
