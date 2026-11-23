#!/bin/bash
set -euo pipefail

for name in database database_test; do
    directory="$name.apitofsim"
    config=datasets.toml
    if [[ "$name" == database_test ]]; then
        config=datasets_test.toml
    fi
    apitofsim db prepare create -w --db-type=super --ase "$config" "$directory"
    tar --zstd -cf "$directory.tar.zst" "$directory"
done
