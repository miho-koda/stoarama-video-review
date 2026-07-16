# Strict 120-second expansion

`scripts/expand_accepted_sources.py` searches the 40 accepted YouTube sources for up to four additional clips per source. It is a separate review campaign: it never rewrites the original 40-row selection file.

Every expansion clip is exactly 120 seconds. The original hard quality gates remain unchanged: 60-pixel people, qualifying density and person-size fractions, daylight, fixed camera, and people-over-vehicles. New clips cannot overlap the original parent interval or another expansion interval; touching intervals are permitted.

The six-way Slurm array is `jobs/expand_accepted_sources.sbatch`; its `0-5%6` array setting limits concurrent GPU workers to six. `jobs/merge_expansion.sbatch` merges the six shards and uploads only reports to `pilotdrive:overnight_scan/expansion_120s_v1/`. Clips upload to shard subfolders and local media are deleted only after link and remote-size verification.

Shortfalls are expected when a source has fewer than four strict candidates in YouTube's rolling DVR window. They are recorded in `expansion_ledger.csv`; they are never filled by relaxing quality rules.
