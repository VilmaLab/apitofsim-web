#!/bin/bash

module load PrgEnv-intel/2026.1.0; module load GCCcore/15.2.0; module load LLVM/21.1.8; module load Meson/1.10.2; module load ModuleView/1.0; module load Development-Tools-and-Diagnostics/sysutils/1.1; module load zstd/1.5.7

USE_TURSO=1 uv run ./ingest.sh
