#!/bin/bash

docker run --rm -v "$(pwd):/tmp/apitofsim-web-ro:Z" \
   "--platform=${DOCKER_PLATFORM}" \
  docker.io/mambaorg/micromamba:2.5.0 /bin/bash -c "\
     cp -r /tmp/apitofsim-web-ro /tmp/apitofsim-web > /dev/null 2>&1; \
     micromamba create --yes --name new_env --file /tmp/apitofsim-web/env.yaml > /dev/null 2>&1 \
     && micromamba env export --name new_env --explicit" > env-container.lock
