#!/bin/env bash

set -euo pipefail
IFS=$'\n\t'

export RCLONE_S3_ENDPOINT=https://a3s.fi 
cd $DATABASE_DIR && \
   rclone copyto --progress :s3,env_auth:apitofsim-data/database.ase.sqlite.db  . && \
   rclone copyto --progress :s3,env_auth:apitofsim-data/database.duckdb  .

export DATABASE="$DATABASE_DIR/database.duckdb"
exec hypercorn -w 1 -b 0.0.0.0:8080 vms:app
