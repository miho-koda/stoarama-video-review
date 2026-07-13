#!/bin/bash
#SBATCH --job-name=stoarama_merge
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=00:20:00
#SBATCH --output=work/overnight/logs/merge_%j.out
#SBATCH --error=work/overnight/logs/merge_%j.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" merge_overnight.py \
  work/overnight/shard_0 work/overnight/shard_1 \
  --output work/overnight/merged \
  --drive-remote pilotdrive:overnight_scan
