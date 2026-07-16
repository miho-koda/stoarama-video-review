#!/bin/bash
#SBATCH --job-name=stoarama_expand_merge
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=00:20:00
#SBATCH --output=work/expansion_120s_v1/logs/merge_%j.out
#SBATCH --error=work/expansion_120s_v1/logs/merge_%j.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
root="${STOARAMA_EXPANSION_WORK_ROOT:-work/expansion_120s_v1}"
shards=()
for index in {0..5}; do shards+=("${root}/shard_${index}"); done
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" scripts/merge_expansion.py "${shards[@]}" \
  --output "${root}/merged" \
  --drive-remote "${STOARAMA_EXPANSION_DRIVE_ROOT:-pilotdrive:overnight_scan/expansion_120s_v1}"
