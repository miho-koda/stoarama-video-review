# Local-only YouTube VOD scan handoff

Read this document completely before running anything. The startup instruction is:

> Read `LOCAL_VOD_HANDOFF.md` completely, run `doctor`, then run the three-video pilot. Do not run the 59-source scan before pilot review.

## Scope and safety contract

`local_vod_scan.py` sequentially scans the 59 archived/VOD candidates in
`manifests/vod_fixed_camera_priority.csv`. It downloads one resumable 144–240p
proxy at a time (hard refusal above 2 GiB), decodes samples only in memory,
fetches at most six promising 720p intervals, and uploads at most two passing
clips per source. It never creates JPG frames. All media is confined to
`~/stoarama-local-vod-scan/current`; uploaded clips are deleted only after the
remote byte size and Drive link have been verified. An interrupt retains only
the current `.part` download. Completed source state is atomically checkpointed.

The working-media ceiling is 3 GiB. `disk_report.json` records peak usage,
downloaded and deleted bytes, and any remaining temporary files. Cookies are
read directly from `chrome:Profile 1`; browser data, rclone configuration,
model weights, and credentials must never be copied into or committed to this
repository. Existing 40-row results and `pilotdrive:overnight_scan` are out of
scope and must remain untouched.

## Install and commands

On macOS with Python 3.11 or newer:

```bash
brew install ffmpeg rclone deno
python3 -m venv .venv-local
source .venv-local/bin/activate
python -m pip install -r requirements-local.txt
python local_vod_scan.py doctor --model /absolute/path/to/yolo26n.pt
python local_vod_scan.py pilot --model /absolute/path/to/yolo26n.pt
```

The pilot IDs are Zagreb `ElW4dUFEpuE`, Antigua `3W0yKMCLiIs`, and Lima
`UwdghOblns0`. Review at most six resulting Drive clips. Only after explicit
pilot approval run:

```bash
python local_vod_scan.py run --model /absolute/path/to/yolo26n.pt
```

Defaults are `chrome:Profile 1`, `pilotdrive:local_vod_review`, and
`~/stoarama-local-vod-scan`. `doctor` is non-mutating and does not resolve or
download a YouTube video. It checks Python, FFmpeg/ffprobe, yt-dlp/Deno,
rclone/Drive read access, Chrome profile readability, model presence, MPS/CPU,
and free disk.

## Acceptance and outputs

The scanner uses `yolo26n.pt` and the established thresholds: daylight,
2–30 people, at least 70% of people 60 px or taller at 720p, people over
vehicles, fixed-camera score, and dense short-burst background-motion checks.
It records global and per-frame person-size distributions (including median
and lower-quartile height) so borderline views are visible in review without
imposing a universal close-view threshold. It requires a five-minute gap between
two clips from one source. PTZ/moving, night,
traffic-dominant, excessive-crowd, undersized-person, and obvious high-view
candidates are rejected. Depth Anything V3 is deliberately deferred. Camera
height is a conservative perspective heuristic with `heuristic` confidence;
absolute 3–4 m height cannot be recovered without calibration. Accepted clips
use the existing 90/120/150-second score policy.

The work directory contains:

- `review_manifest.csv`: no more than two rows per passing source; location,
  source/review and Drive links, offsets, UTC only where YouTube metadata makes
  it supportable, duration, all quality metrics, PTZ/camera assessment, public
  access warning, and run revision.
- `scan_ledger.csv`: every attempted source, retries, media disposition,
  upload count, byte accounting, and explicit rejection/error reason.
- `disk_report.json`: peak temporary footprint, traffic totals, and cleanup
  confirmation.

These are review candidates. Never merge them automatically into the accepted
40-row dataset.

Run local tests with `python -m pytest -q test_local_vod_scan.py`. Pilot
acceptance additionally requires no isolated frames, peak use below 3 GiB,
clickable Drive clips of exactly 90/120/150 seconds, and no excluded scene
classes above.
