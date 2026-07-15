# Original server workflow

This workflow produced the accepted 40 clips. It is retained for auditability; do not launch a new catalog campaign without a separate decision.

## 1. Discover Stoarama sources

`scripts/overnight_scan.py` obtains a Stoarama catalog and uses `src/stoarama_pipeline/` to normalize source records, classify links, and prioritize YouTube, HLS, and direct-video sources.

## 2. Scan source windows on the GPU server

The Slurm files in `jobs/` start independent scan shards. `overnight_scan.py` samples windows; `youtube_dvr_scan.py` handles accessible historical YouTube DVR segments; YOLO and shared scoring evaluate people, vehicles, daylight, and camera stability.

## 3. Preserve accepted clips

For a passing interval, the scanner selects a 90-, 120-, or 150-second clip, checks its duration, uploads it to Drive, verifies the link, and writes a source ledger. Local files belong under ignored `work/`.

## 4. Merge results

`scripts/merge_overnight.py` combines shards into `selections_all.csv` (40 accepted clips), `review_balanced.csv` (23 geographically capped review clips), and `scan_ledger.csv` (source outcomes). The job files are the executable launch reference and require private model and Drive configuration.
