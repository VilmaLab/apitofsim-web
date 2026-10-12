#!/bin/bash
set -euo pipefail

export UV_PROJECT_ENVIRONMENT=.venv-local

uv sync --locked

uv pip install \
  --python .venv-local/bin/python \
  meson-python 'meson>=1.10' ninja

uv pip install \
  --python .venv-local/bin/python \
  --no-build-isolation \
  -Csetup-args=-Dbuildtype=debugoptimized \
  -Ceditable-verbose=true \
  --editable '../apitofsim[plot]'

uv pip install \
  --python .venv-local/bin/python \
  --no-sources-package apitofsim \
  --no-build-isolation \
  -Csetup-args=-Dbuildtype=debugoptimized \
  -Ceditable-verbose=true \
  --editable '../apitofsim-resultviewer/'
