#!/bin/bash
#SBATCH --job-name=stoarama_exchange
#SBATCH --partition=mit_preemptable
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=06:00:00
#SBATCH --requeue
#SBATCH --output=work/overnight/logs/exchange_%j.out
#SBATCH --error=work/overnight/logs/exchange_%j.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
mkdir -p "${STOARAMA_EXCHANGE:?Set STOARAMA_EXCHANGE}" work/overnight/logs
rclone copy "${STOARAMA_DRIVE_EXCHANGE:?Set STOARAMA_DRIVE_EXCHANGE}" "$STOARAMA_EXCHANGE"
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" vod_exchange_gpu.py \
  "${STOARAMA_EXCHANGE_STAGE:?Set STOARAMA_EXCHANGE_STAGE}" \
  --exchange "$STOARAMA_EXCHANGE" \
  --input "${STOARAMA_EXCHANGE_INPUT:?Set STOARAMA_EXCHANGE_INPUT}" \
  --output "${STOARAMA_EXCHANGE_OUTPUT:?Set STOARAMA_EXCHANGE_OUTPUT}" \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" --device 0
rclone copyto "$STOARAMA_EXCHANGE_OUTPUT" "${STOARAMA_DRIVE_EXCHANGE%/}/$(basename "$STOARAMA_EXCHANGE_OUTPUT")"
