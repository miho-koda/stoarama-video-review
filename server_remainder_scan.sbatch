#!/bin/bash
#SBATCH --job-name=stoarama_remainder
#SBATCH --partition=mit_preemptable
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=work/server_remainder/logs/scan_%A_%a.out
#SBATCH --error=work/server_remainder/logs/scan_%A_%a.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
mkdir -p work/server_remainder/logs
module load community-modules
module load ffmpeg/5.1.4

shard="${SLURM_ARRAY_TASK_ID:?Submit as an array}"
work="${STOARAMA_WORK_ROOT:?Set STOARAMA_WORK_ROOT}/shard_${shard}"
remote="${STOARAMA_DRIVE_ROOT:?Set STOARAMA_DRIVE_ROOT}/shard_${shard}"
mkdir -p "${work}/logs"

"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" overnight_scan.py \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --work "${work}" \
  --hours 23.5 \
  --device 0 \
  --drive-remote "${remote}" \
  --shard-count "${STOARAMA_SHARD_COUNT:?Set STOARAMA_SHARD_COUNT}" \
  --shard-index "${shard}" \
  --source-manifest "${STOARAMA_SOURCE_MANIFEST:?Set STOARAMA_SOURCE_MANIFEST}"
