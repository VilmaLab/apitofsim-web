#!/bin/bash
#SBATCH -t 12:00:00
#SBATCH --job-name=prepare-database
#SBATCH --exclusive
#SBATCH --mem=0
#SBATCH --nodes=1

set -euo pipefail
IFS=$'\n\t'

echo "Got allocated resources:"
echo "  Job ID: ${SLURM_JOB_ID-unset}"
echo "  Nodes: ${SLURM_JOB_NODELIST-unset}"
echo "  CPUs: ${SLURM_CPUS_ON_NODE-unset}"
echo "  Memory: ${SLURM_MEM_PER_NODE-unset} MB"

module load PrgEnv-intel/2026.1.0; module load GCCcore/15.2.0; module load LLVM/21.1.8; module load Meson/1.10.2; module load ModuleView/1.0; module load Development-Tools-and-Diagnostics/sysutils/1.1; module load zstd/1.5.7

export PTABLE_INTERACTIVE=0
UV_PROJECT=~/vms uv run ./prepare.sh
