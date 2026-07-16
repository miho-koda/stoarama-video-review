#!/bin/bash
#SBATCH --job-name=stoarama_expand120
#SBATCH --partition=mit_normal_gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=06:00:00
#SBATCH --array=0-5%6
#SBATCH --output=work/expansion_120s_v1/logs/scan_%A_%a.out
#SBATCH --error=work/expansion_120s_v1/logs/scan_%A_%a.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
module load community-modules
module load ffmpeg/5.1.4
root="${STOARAMA_EXPANSION_WORK_ROOT:-work/expansion_120s_v1}"
work="${root}/shard_${SLURM_ARRAY_TASK_ID}"
mkdir -p "${work}" "${root}/logs"
rclone="${HOME}/.local/bin/rclone"
input="${work}/selections_all.final_enriched.csv"
"${rclone}" copyto "${STOARAMA_EXPANSION_INPUT_REMOTE:-pilotdrive:overnight_scan/selections_all.final_enriched.csv}" "${input}"
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" scripts/expand_accepted_sources.py \
  --input "${input}" \
  --work "${work}" \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --device 0 \
  --drive-remote "${STOARAMA_EXPANSION_DRIVE_ROOT:-pilotdrive:overnight_scan/expansion_120s_v1}/shard_${SLURM_ARRAY_TASK_ID}/clips" \
  --shard-count 6 --shard-index "${SLURM_ARRAY_TASK_ID}" \
  --max-sources "${STOARAMA_EXPANSION_MAX_SOURCES:-0}" --hours 5.5
