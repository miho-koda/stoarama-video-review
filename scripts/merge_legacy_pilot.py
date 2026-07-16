#!/usr/bin/env python3
"""Merge ten preserved legacy-pilot clips with the canonical 40-row selection."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from _paths import PROJECT_ROOT


PROVENANCE_FIELDS = [
    "selection_origin", "selection_policy", "quality_gate_status", "legacy_filename",
    "legacy_timestamp_accuracy", "legacy_timestamp_source", "legacy_annotation_score",
    "recording_start_local", "recording_end_local", "legacy_street_or_area", "legacy_timezone", "legacy_status",
]


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def overlaps(first: dict, second: dict) -> bool:
    return utc(first["segment_start_utc"]) < utc(second["segment_end_utc"]) and utc(second["segment_start_utc"]) < utc(first["segment_end_utc"])


def pilot_row(pilot: dict, row_id: int) -> dict:
    start, end = pilot["recording_start_utc"], pilot["recording_end_utc"]
    return {
        "row_id": str(row_id), "source_key": f"youtube:{pilot['source_youtube_url'].split('v=')[-1]}:pilot:{pilot['row_id']}",
        "stream_id": "", "name": pilot["name"], "capture_type": "youtube_watch",
        "video_id": pilot["source_youtube_url"].split("v=")[-1], "youtube_url": pilot["source_youtube_url"],
        "source_page_url": pilot["source_youtube_url"], "source_url": pilot["source_youtube_url"], "stoarama_url": "",
        "city": pilot["city"], "region": pilot["region"], "country": pilot["country"],
        "location_text": ", ".join(value for value in (pilot["street_or_area"], pilot["city"], pilot["region"], pilot["country"]) if value),
        "segment_start_utc": start, "segment_end_utc": end, "duration_seconds": pilot["duration_seconds"],
        "score": pilot["annotation_score"], "people_min": pilot["people_min"], "people_median": pilot["people_median"],
        "people_max": pilot["people_max"], "provenance": "pilot_legacy_youtube_dvr", "local_path": "",
        "drive_url": pilot["drive_url"], "upload_status": "uploaded", "link_status": "verified", "status": "selected",
        "stoarama_city_original": pilot["city"], "stoarama_region_original": pilot["region"],
        "stoarama_country_original": pilot["country"], "verified_city": "", "verified_region": "",
        "verified_country": "", "verified_location_text": "", "location_status": "not_revalidated",
        "location_confidence": "", "location_evidence_url": pilot["source_youtube_url"],
        "location_evidence_method": "legacy pilot metadata", "location_reviewed_at": "",
        "location_reviewer": "", "location_notes": "Legacy pilot location retained; not revalidated in the later audit.",
        "selection_origin": "pilot_legacy", "selection_policy": "legacy_pilot",
        "quality_gate_status": "not_revalidated", "legacy_filename": pilot["filename"],
        "legacy_timestamp_accuracy": pilot["timestamp_accuracy"], "legacy_timestamp_source": pilot["timestamp_source"],
        "legacy_annotation_score": pilot["annotation_score"], "recording_start_local": pilot["recording_start_local"],
        "recording_end_local": pilot["recording_end_local"], "legacy_street_or_area": pilot["street_or_area"],
        "legacy_timezone": pilot["timezone"], "legacy_status": pilot["status"],
    }


def merge(current: list[dict], pilot: list[dict]) -> list[dict]:
    current = [dict(row) for row in current]
    for row in current:
        row.update({"selection_origin": "overnight_server_scan", "selection_policy": "overnight_strict_v1",
                    "quality_gate_status": "automated_strict_pass", "legacy_filename": "",
                    "legacy_timestamp_accuracy": "approximately ±5 seconds", "legacy_timestamp_source": "YouTube live DVR fragment clock",
                    "legacy_annotation_score": "", "recording_start_local": "", "recording_end_local": "",
                    "legacy_street_or_area": "", "legacy_timezone": "", "legacy_status": ""})
    additions = [pilot_row(row, len(current) + index) for index, row in enumerate(pilot, 1)]
    for candidate in additions:
        for existing in current:
            if candidate["youtube_url"] == existing.get("youtube_url") and overlaps(candidate, existing):
                raise ValueError(f"pilot interval overlaps existing selection: {candidate['legacy_filename']}")
    return current + additions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current, fields = read_csv(args.selection)
    pilot, _ = read_csv(args.pilot)
    if len(current) != 40 or len(pilot) != 10:
        raise SystemExit("expected exactly 40 current rows and 10 legacy pilot rows")
    merged = merge(current, pilot)
    output_fields = list(dict.fromkeys(fields + PROVENANCE_FIELDS))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(merged)
    print(f"merged_rows={len(merged)} output={args.output}")


if __name__ == "__main__":
    main()
