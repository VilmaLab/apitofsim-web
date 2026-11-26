#!/bin/bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Database creation and compression require fresh output paths.
rm -f -- database{,_test}.duckdb{,.wal} \
    database{,_test}.ase.sqlite.db{,.lock,-journal,-wal,-shm} \
    database.duckdb.zst database.ase.sqlite.db.zst
