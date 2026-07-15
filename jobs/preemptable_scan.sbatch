#!/bin/bash
#SBATCH --job-name=stoarama_preempt
#SBATCH --partition=mit_preemptable
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=work/overnight/logs/preempt_%A_%r.out
#SBATCH --error=work/overnight/logs/preempt_%A_%r.err

set -euo pipefail
cd "${STOARAMA_REPO:?Set STOARAMA_REPO}"
mkdir -p work/overnight/logs
module load community-modules
module load ffmpeg/5.1.4
prior_args=()
IFS=: read -r -a prior_ledgers <<< "${STOARAMA_PRIOR_LEDGERS:-}"
for ledger in "${prior_ledgers[@]}"; do
  [[ -n "$ledger" ]] && prior_args+=(--prior-ledger "$ledger")
done
manifest_args=()
[[ -n "${STOARAMA_SOURCE_MANIFEST:-}" ]] && manifest_args=(--source-manifest "$STOARAMA_SOURCE_MANIFEST")

# The scanner atomically rewrites its ledger after every source. If Slurm
# preempts and requeues this job, the new process skips every ledgered source;
# at most the source active at the instant of preemption is repeated.
"${STOARAMA_PYTHON:?Set STOARAMA_PYTHON}" scripts/overnight_scan.py \
  --model "${STOARAMA_MODEL:?Set STOARAMA_MODEL}" \
  --work "${STOARAMA_WORK:?Set STOARAMA_WORK}" \
  --hours 23.75 \
  --device 0 \
  --drive-remote "${STOARAMA_DRIVE_REMOTE:-pilotdrive:overnight_scan}" \
  --shard-count "${STOARAMA_SHARD_COUNT:?Set STOARAMA_SHARD_COUNT}" \
  --shard-index "${STOARAMA_SHARD_INDEX:?Set STOARAMA_SHARD_INDEX}" \
  "${prior_args[@]}" "${manifest_args[@]}"
