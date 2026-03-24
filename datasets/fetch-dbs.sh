#!/bin/env bash

set -euo pipefail
IFS=$'\n\t'

export RCLONE_S3_ENDPOINT=https://a3s.fi
rclone copyto --progress :s3,env_auth:apitofsim-data/database.ase.sqlite.db  .
rclone copyto --progress :s3,env_auth:apitofsim-data/database.duckdb  .
