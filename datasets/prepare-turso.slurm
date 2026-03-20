#!/bin/bash
#SBATCH -t 12:00:00
#SBATCH --job-name=prepare-database
#SBATCH --partition=short
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

module load LLVM/18.1.8-GCCcore-13.3.0; module load Meson/1.8.2-GCCcore-14.3.0

export PTABLE_INTERACTIVE=0
UV_PROJECT=~/vms uv run ./prepare.sh
