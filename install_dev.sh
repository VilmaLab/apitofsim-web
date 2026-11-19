#!/bin/bash
set -euo pipefail

export UV_PROJECT_ENVIRONMENT=.venv-local

uv sync --locked

uv pip install \
  --python .venv-local/bin/python \
  'scikit-build-core>=0.11' 'nanobind==2.9.2' 'cmake>=3.28' ninja

uv pip install \
  --python .venv-local/bin/python \
  --no-build-isolation \
  -Ccmake.build-type=RelWithDebInfo \
  --editable '../apitofsim[plot]'

uv pip install \
  --python .venv-local/bin/python \
  --no-sources-package apitofsim \
  --editable '../apitofsim-resultviewer/'
