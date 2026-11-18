#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 || ( ${1:-} == -* && ${1:-} != --dependencies-only ) ]]; then
  echo "Usage: $0 [IMAGE | --dependencies-only]" >&2
  exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
engine=${CONTAINER_ENGINE:-docker}
platform=linux/amd64
registry_repository=${PUBLISH_DEPENDENCIES_TO:-}
repository=${registry_repository:-localhost/apitofsim-web-dependencies}
checksum=(sha256sum)
if ! command -v sha256sum >/dev/null; then
  checksum=(shasum -a 256)
fi
key=$("${checksum[@]}" Dockerfile pyproject.toml uv.lock | "${checksum[@]}")
dependency_image="$repository:${key%% *}"

# Check the registry first when publishing so another builder can reuse its image.
if [[ -z "$registry_repository" ]] || ! "$engine" pull --platform "$platform" "$dependency_image" >&2; then
  if ! "$engine" image inspect "$dependency_image" >/dev/null 2>&1; then
    "$engine" build --platform "$platform" --target dependencies --tag "$dependency_image" . >&2
  fi
  if [[ -n "$registry_repository" ]]; then
    "$engine" push "$dependency_image" >&2
  fi
fi

if [[ ${1:-} == --dependencies-only ]]; then
  printf '%s\n' "$dependency_image"
else
  "$engine" build --platform "$platform" --build-arg "DEPENDENCY_IMAGE=$dependency_image" \
    --tag "${1:-apitofsim-web}" .
fi
