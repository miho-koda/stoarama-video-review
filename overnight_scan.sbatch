#!/bin/bash
#SBATCH --job-name=stoarama_all
#SBATCH --partition=mit_normal_gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=06:00:00
#SBATCH --output=work/overnight/logs/scan_%j.out
#SBATCH --error=work/overnight/logs/scan_%j.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
mkdir -p work/overnight/logs
module load community-modules
module load ffmpeg/5.1.4
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" overnight_scan.py \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --work "${STOARAMA_WORK:-work/overnight}" \
  --hours 5.75 \
  --device 0 \
  --drive-remote "${STOARAMA_DRIVE_REMOTE:-pilotdrive:overnight_scan}" \
  --shard-count "${STOARAMA_SHARD_COUNT:-1}" \
  --shard-index "${STOARAMA_SHARD_INDEX:-0}"
