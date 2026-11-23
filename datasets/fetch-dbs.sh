#!/bin/env bash

set -euo pipefail
IFS=$'\n\t'

export RCLONE_S3_ENDPOINT=https://a3s.fi
name=database.apitofsim
stage=$(mktemp -d -- .download.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
rclone copyto --progress ":s3,env_auth:apitofsim-data/$name.tar.zst" "$stage/$name.tar.zst"
tar --zstd -xf "$stage/$name.tar.zst" -C "$stage"
[[ -f "$stage/$name/database.duckdb" && -f "$stage/$name/ase.sqlite.db" ]]
mv -T -- "$stage/$name" "$name"
