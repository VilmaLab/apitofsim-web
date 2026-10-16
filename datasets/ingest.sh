#!/bin/bash
shopt -s globstar
set -euo pipefail
IFS=$'\n\t'

export RCLONE_S3_ENDPOINT=https://a3s.fi
rclone copy :s3,env_auth:apitofsim-data .

for ds in alfaouri2022 atomprod besel2020 tunning; do
    mkdir -p "$ds"
    zstd -dc "$ds.zstd" | tar -x -C "$ds"
done

(cd alfaouri2022 && apitofsim generate pathways --guess-prefix gaussian pathways.csv clusters.csv **/*.log)
(cd atomprod && apitofsim generate pathways --guess-prefix orca pathways.csv clusters.csv **/*.out)
(cd besel2020 && apitofsim generate pathways --guess-prefix xyz pathways.csv clusters.csv **/*.xyz)
(cd tunning && apitofsim generate pathways --guess-prefix gaussian pathways.csv clusters.csv **/*.log)

rm -f database.db
if [ -n "${USE_TURSO}" ]; then
    sbatch --wait prepare-turso.slurm
else
    bash ./prepare.sh
fi

rclone copy database.duckdb :s3,env_auth:apitofsim-data
rclone copy database.ase.sqlite.db :s3,env_auth:apitofsim-data
rclone copy database_test.duckdb :s3,env_auth:apitofsim-data-public
rclone copy database_test.ase.sqlite.db :s3,env_auth:apitofsim-data-public
