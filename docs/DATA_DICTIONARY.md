# Data dictionary

The canonical Drive file retains original selection columns and appends current source metadata, reviewed locations, and public YouTube metadata where available.

## Selection fields

`source_key`, `stream_id`, and `video_id` identify a source. URLs identify its original source and review page. `segment_start_utc`, `segment_end_utc`, and `duration_seconds` identify the clip. Score, people, daylight, fixed-camera, social, and vehicle fields record automated selection evidence where available. `drive_url`, `upload_status`, `link_status`, and `status` record preservation outcome.

`selection_origin`, `selection_policy`, and `quality_gate_status` distinguish the 40 later strict-server rows from the ten preserved legacy-pilot rows. Fields beginning `legacy_` retain pilot-only filename, timing, location, score, and status details without pretending that missing later metrics were measured.

## Locations

`city`, `region`, `country`, and `location_text` are original source values and are never overwritten. The `verified_*` fields are reviewed values. `location_status`, `location_confidence`, `location_evidence_url`, `location_evidence_method`, `location_reviewed_at`, `location_reviewer`, and `location_notes` document the correction.

## Added metadata

`stoarama_*` fields are a timestamped current Stoarama snapshot. `youtube_*` fields are current public YouTube metadata where available. Detailed YouTube values can be blank when server access triggers YouTube bot restrictions; blanks are not inferred values.
