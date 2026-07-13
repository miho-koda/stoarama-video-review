# Legacy pilot artifacts

The root-level `initial_*`, `pilot_pool.csv`, `youtube_review_candidates.csv`,
`youtube_review_10.csv`, `scan.py`, and versioned review pages are retained for
compatibility with the completed ten-clip pilot and previously shared URLs.

New runs should use `pipeline.py`, `pipeline_config.json`, and
`mac_download_pilot.py --manifest ...`. Do not use the legacy manifests as
inputs for a production scan.
