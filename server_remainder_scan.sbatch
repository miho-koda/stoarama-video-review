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

"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" overnight_scan.py \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --work "${STOARAMA_WORK:?Set STOARAMA_WORK}" \
  --hours 23.5 \
  --device 0 \
  --drive-remote "${STOARAMA_DRIVE_REMOTE:?Set STOARAMA_DRIVE_REMOTE}" \
  --shard-count "${STOARAMA_SHARD_COUNT:?Set STOARAMA_SHARD_COUNT}" \
  --shard-index "${SLURM_ARRAY_TASK_ID:?Submit as an array}" \
  --source-manifest "${STOARAMA_SOURCE_MANIFEST:?Set STOARAMA_SOURCE_MANIFEST}"
