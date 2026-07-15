# Stoarama social-mixing video pipeline

This repository turns Stoarama's YouTube, HLS, and HTTP-video catalog into
reviewable, timestamped social-mixing clips and metadata. YouTube preservation
can still require a Mac when cloud IPs are blocked.

Use Python 3.11 or newer for both stages.

The pipeline never downloads an entire livestream. It samples historical DVR
fragments during selection and preserves only accepted 90, 120, or 150-second
intervals.

## Acceptance criteria

- Fixed camera; moving/PTZ views are rejected.
- Broad local daytime plus image-based daylight checks.
- Normally 2–30 qualifying people per sampled frame.
- At least 70% of detected people are 60 pixels or taller.
- People dominate vehicles.
- Moderate, annotatable density is preferred over sparse or packed crowds.
- Nearby-person and active-density signals improve the social-mixing rank.
- Weak videos are rejected rather than used to pad the target.

Thresholds and the 90/120/150-second score policy live in
[`pipeline_config.json`](pipeline_config.json).

## Stage 1: discover on the server

The discovery command reads Stoarama's current API, extracts YouTube IDs,
deduplicates them, and checkpoints a normalized catalog.

```bash
python pipeline.py discover --output work/catalog.csv
```

Stoarama does not currently expose an IANA timezone for every record. The
catalog includes editable `timezone` and `utc_offset_hours` columns. Populate
`utc_offset_hours` for multi-time-zone countries when known; otherwise the
selector uses image daylight only and does not guess a local clock. Stable
single-zone country fallbacks may be added to `pipeline_config.json`.

For a quick smoke test:

```bash
python pipeline.py discover --max-records 20 --output work/catalog.csv
```

For a normal run, discovery and resumable GPU selection can be launched as one
command:

```bash
python pipeline.py run \
  --model /absolute/path/to/yolo26n.pt \
  --target 80 \
  --device 0
```

Add `--refresh-catalog` when you intentionally want to replace the cached
Stoarama catalog. The separate commands below remain available for debugging.

## Stage 2: select on the GPU server

Install `requirements-server.txt`, then provide the project YOLO checkpoint.
The command resumes from `work/selections.csv` and `work/rejected.csv` after an
interruption.

```bash
python pipeline.py select \
  --catalog work/catalog.csv \
  --model /absolute/path/to/yolo26n.pt \
  --target 80 \
  --device 0 \
  --output work/selections.csv \
  --rejected work/rejected.csv

python pipeline.py validate work/selections.csv
```

Use `--max-videos 10` for a bounded test and `--no-resume` to intentionally
start a selection file again.

## Stage 3: preserve and upload on macOS

Install the local runtime once:

```bash
brew install ffmpeg rclone deno
python -m pip install -U yt-dlp
```

Configure an rclone Drive remote once. The Drive folder ID is configuration,
not a credential:

```bash
rclone config create pilotdrive drive scope drive root_folder_id YOUR_FOLDER_ID
```

Download the current script and run it with the server-generated manifest:

```bash
python mac_download_pilot.py \
  --manifest work/selections.csv \
  --browser chrome \
  --upload \
  --drive-remote pilotdrive:
```

The preservation stage processes oldest selections first, resumes existing
MP4s, enables yt-dlp's Deno/EJS challenge solver, validates encoded duration,
uploads each clip, creates a Drive link, and writes `pilot_manifest.csv` with
UTC/local timestamps and source metadata.

## Overnight all-source scan

`overnight_scan.py` inventories all motion-video source types, interleaves
countries, searches existing Stoarama recordings before trying a live capture,
and checkpoints after every source. Accepted MP4s are cached under
`work/overnight/clips/` and uploaded to the configured Drive remote. YouTube
selections that cannot be preserved on the server are written to
`needs_mac_download.csv`.

Submit two independent six-hour shards concurrently, then merge their outputs:

```bash
mkdir -p work/overnight/logs
export STOARAMA_REPO="$PWD"
export STOARAMA_PYTHON="$HOME/.stoarama-server-env/bin/python"
export STOARAMA_MODEL="/absolute/path/to/yolo26n.pt"
export STOARAMA_SHARD_COUNT=2
export STOARAMA_WORK=work/overnight/shard_0
export STOARAMA_DRIVE_REMOTE=pilotdrive:overnight_scan/shard_0
export STOARAMA_SHARD_INDEX=0
first=$(sbatch --parsable overnight_scan.sbatch)
export STOARAMA_WORK=work/overnight/shard_1
export STOARAMA_DRIVE_REMOTE=pilotdrive:overnight_scan/shard_1
export STOARAMA_SHARD_INDEX=1
second=$(sbatch --parsable overnight_scan.sbatch)
sbatch --dependency="afterany:$first:$second" merge_overnight.sbatch
```

Morning outputs are `review_balanced.csv`, `selections_all.csv`,
`scan_ledger.csv`, and `needs_mac_download.csv`. The balanced review contains
at most 80 verified Drive clips and caps each country at five rows.

## Reproducibility and safety

## Local-only VOD recovery on macOS

The active archived-YouTube workflow runs entirely on the Mac: no frame packs
or browser credentials leave it, and no JPG frames are created. Read
[`LOCAL_VOD_HANDOFF.md`](LOCAL_VOD_HANDOFF.md), run `doctor`, and then run the
fixed three-source pilot. Do not start the 59-source run until its at-most-six
clips have been reviewed and approved.

```bash
python local_vod_scan.py doctor --model /absolute/path/to/yolo26n.pt
python local_vod_scan.py pilot --model /absolute/path/to/yolo26n.pt
# only after pilot approval:
python local_vod_scan.py run --model /absolute/path/to/yolo26n.pt
```

## Server-only catalog remainder

Keep the local VOD manifest and accepted sources out of the server campaign.
Build a frozen remainder manifest, then submit separate shards with a separate
work directory and Drive root. This does not touch `overnight_scan`.

```bash
python build_server_remainder_manifest.py \
  --catalog work/overnight/catalog_all.csv \
  --exclude manifests/vod_fixed_camera_priority.csv \
  --exclude work/overnight/merged/selections_all.csv \
  --output work/server_remainder/source_manifest.csv
```

- No Google, YouTube, or rclone credentials are stored in this repository.
- `work/`, clips, cookies, model weights, caches, and rclone configuration are
  ignored by Git.
- Selection output is written atomically after every examined video.
- Recording timestamps come from YouTube DVR fragment timing and should be
  treated as approximately ±5 seconds.
- Existing versioned review pages and pilot CSVs remain only for compatibility;
  see [`legacy/README.md`](legacy/README.md).

## Tests

```bash
python -m unittest -v test_pipeline.py
python -m pytest -q test_scan.py
```
