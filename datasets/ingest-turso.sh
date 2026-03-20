#!/bin/bash

module load p7zip LLVM/18.1.8-GCCcore-13.3.0; module load Meson/1.8.2-GCCcore-14.3.0
USE_TURSO=1 uv run ./ingest.sh
