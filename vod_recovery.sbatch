#!/bin/bash
#SBATCH --job-name=stoarama_vod
#SBATCH --partition=mit_preemptable
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=work/overnight/logs/vod_%j.out
#SBATCH --error=work/overnight/logs/vod_%j.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
mkdir -p work/overnight/logs
module load community-modules
module load ffmpeg/5.1.4
export PATH="$HOME/.deno/bin:$PATH"
export STOARAMA_VOD_FFMPEG="${STOARAMA_VOD_FFMPEG:-$HOME/.stoarama-ffmpeg/bin/ffmpeg}"
cookie_args=()
[[ -n "${STOARAMA_YOUTUBE_COOKIES:-}" ]] && cookie_args=(--cookies "$STOARAMA_YOUTUBE_COOKIES")
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" vod_recovery_scan.py \
  --input "${STOARAMA_SOURCE_MANIFEST:?Set STOARAMA_SOURCE_MANIFEST}" \
  --work "${STOARAMA_WORK:?Set STOARAMA_WORK}" \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --device 0 \
  --drive-remote "${STOARAMA_DRIVE_REMOTE:?Set STOARAMA_DRIVE_REMOTE}" \
  --shard-count "${STOARAMA_SHARD_COUNT:?Set STOARAMA_SHARD_COUNT}" \
  --shard-index "${STOARAMA_SHARD_INDEX:?Set STOARAMA_SHARD_INDEX}" \
  "${cookie_args[@]}"
