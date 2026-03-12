#!/bin/bash
shopt -s globstar
set -euo pipefail
IFS=$'\n\t'

RCLONE_S3_ENDPOINT=https://a3s.fi rclone copy :s3,env_auth:apitofsim-data .

7z x -y alfaouri2022.7z -oalfaouri2022
7z x -y atomprod.7z -oatomprod
7z x -y besel2020.7z -obesel2020
7z x -y tunning.7z -otunning

(cd alfaouri2022 && apitofsim generate pathways --guess-prefix gaussian pathways.csv clusters.csv **/*.log)
(cd atomprod && apitofsim generate pathways --guess-prefix orca pathways.csv clusters.csv **/*.out)
(cd besel2020 && apitofsim generate pathways --guess-prefix xyz pathways.csv clusters.csv **/*.xyz)
(cd tunning && apitofsim generate pathways --guess-prefix gaussian pathways.csv clusters.csv **/*.log)

rm -f database.db
apitofsim db prepare create --db-type=super --ase datasets.toml database.duckdb
