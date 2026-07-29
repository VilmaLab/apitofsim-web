#!/bin/bash

apitofsim db prepare create -w --db-type=super --ase datasets.toml database.duckdb
apitofsim db prepare create -w --db-type=super --ase datasets_test.toml database_test.duckdb
