#!/bin/bash
set -euo pipefail

# The lock is consumed by the Dockerfile, which builds for x86_64.
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"

# Write to a temporary file so a failed run does not clobber the existing lock.
tmp="$(mktemp ./env-container.lock.XXXXXX)"
chmod 644 "$tmp" # readable by the container's unprivileged user
trap 'rm -f "$tmp"' EXIT

docker run --rm -v "$(pwd):/tmp/apitofsim-web-ro:Z" \
   "--platform=${DOCKER_PLATFORM}" \
  docker.io/mambaorg/micromamba:2.5.0 /bin/bash -c "\
     set -e; \
     cp -r /tmp/apitofsim-web-ro /tmp/apitofsim-web; \
     micromamba create --yes --name new_env --file /tmp/apitofsim-web/env.yaml >&2 \
     && micromamba env export --name new_env --explicit" > "$tmp"

grep -q '^@EXPLICIT' "$tmp" || {
  echo "error: micromamba produced no lock" >&2
  exit 1
}

mv "$tmp" env-container.lock
trap - EXIT
