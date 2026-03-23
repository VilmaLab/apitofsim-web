#!/bin/bash

set -euo pipefail
IFS=$'\n\t'

cd $1 && RCLONE_S3_ENDPOINT=https://a3s.fi rclone copy :s3,env_auth:apitofsim-data .
