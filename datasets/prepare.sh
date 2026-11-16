#!/bin/bash

apitofsim db prepare create -w --db-type=super --ase datasets.toml database.duckdb
apitofsim db prepare create -w --db-type=super --ase datasets_test.toml database_test.duckdb
zstdmt --rm database.duckdb -o database.duckdb.zst
zstdmt --rm database.ase.sqlite.db -o database.ase.sqlite.db.zst
