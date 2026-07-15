#!/usr/bin/env python3
"""Append current Stoarama and optional local YouTube metadata to accepted clips.

The original selection columns are preserved.  Location corrections are never
guessed or overwritten: source-provided values and review fields coexist.
"""
from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


STOARAMA_FIELDS = [
    "stoarama_snapshot_at_utc", "stoarama_found", "stoarama_provider", "stoarama_external_id",
    "stoarama_source_family", "stoarama_execution_class", "stoarama_source_url_current",
    "stoarama_source_page_url_current", "stoarama_tags", "stoarama_location_source",
    "stoarama_location_locality", "stoarama_location_text_current", "stoarama_recording_state",
    "stoarama_expected_fps", "stoarama_runtime_status", "stoarama_captures_success_current",
    "stoarama_survey_people_current", "stoarama_survey_vehicles_current", "stoarama_survey_sampled_at",
    "stoarama_import_source", "stoarama_import_list", "stoarama_import_row_number",
    "stoarama_import_valid", "stoarama_import_why", "stoarama_import_verified_at",
]
LOCATION_FIELDS = [
    "stoarama_city_original", "stoarama_region_original", "stoarama_country_original",
    "verified_city", "verified_region", "verified_country", "verified_location_text",
    "location_status", "location_confidence", "location_evidence_url", "location_evidence_method",
    "location_reviewed_at", "location_reviewer", "location_notes",
]
YOUTUBE_FIELDS = [
    "youtube_metadata_checked_at_utc", "youtube_metadata_status", "youtube_current_title",
    "youtube_description", "youtube_channel", "youtube_channel_id", "youtube_channel_url",
    "youtube_uploader", "youtube_uploader_id", "youtube_upload_date", "youtube_release_timestamp",
    "youtube_duration_seconds_current", "youtube_live_status", "youtube_availability",
    "youtube_webpage_url_current", "youtube_thumbnail", "youtube_categories", "youtube_tags",
    "youtube_language", "youtube_view_count", "youtube_like_count", "youtube_error",
]


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def atomic_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def fetch_stoarama(api: str) -> dict[str, dict]:
    streams, offset = {}, 0
    while True:
        query = urllib.parse.urlencode({"limit": 500, "offset": offset, "include_image_urls": "false",
                                        "capture_types": "youtube_watch,hls,http_video"})
        request = urllib.request.Request(f"{api}?{query}", headers={"Accept": "application/json",
                                                                      "User-Agent": "social-mixing-metadata-audit/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        batch = payload.get("items") or []
        for item in batch:
            stream = item.get("stream") or {}
            if stream.get("id") is not None:
                streams[str(stream["id"])] = {"stream": stream, "item": item}
        if not batch or offset + len(batch) >= int(payload.get("total") or 0):
            break
        offset += len(batch)
    return streams


def blank_fields(row: dict, fields: list[str]) -> None:
    for field in fields:
        row.setdefault(field, "")


def add_stoarama(row: dict, record: dict | None, snapshot_at: str) -> None:
    blank_fields(row, STOARAMA_FIELDS + LOCATION_FIELDS)
    row["stoarama_snapshot_at_utc"] = snapshot_at
    row["stoarama_city_original"] = row.get("city", "")
    row["stoarama_region_original"] = row.get("region", "")
    row["stoarama_country_original"] = row.get("country", "")
    if not row.get("location_status"):
        row["location_status"] = "unverified"
    if not record:
        row["stoarama_found"] = "false"
        return
    stream, item = record["stream"], record["item"]
    metadata = stream.get("metadata_json") or {}
    csv_values = metadata.get("csv_values") if isinstance(metadata, dict) else {}
    csv_values = csv_values if isinstance(csv_values, dict) else {}
    row.update({
        "stoarama_found": "true", "stoarama_provider": stream.get("provider") or "",
        "stoarama_external_id": stream.get("external_id") or "", "stoarama_source_family": stream.get("source_family") or "",
        "stoarama_execution_class": stream.get("execution_class") or "", "stoarama_source_url_current": stream.get("source_url") or "",
        "stoarama_source_page_url_current": stream.get("source_page_url") or "", "stoarama_tags": json.dumps(stream.get("tags") or [], ensure_ascii=False),
        "stoarama_location_source": stream.get("location_source") or "", "stoarama_location_locality": stream.get("location_locality") or "",
        "stoarama_location_text_current": stream.get("location_text") or "", "stoarama_recording_state": stream.get("recording_state") or "",
        "stoarama_expected_fps": stream.get("expected_fps") or "", "stoarama_runtime_status": stream.get("capture_runtime_status") or "",
        "stoarama_captures_success_current": item.get("captures_success") or 0,
        "stoarama_survey_people_current": item.get("survey_last_person_count") if item.get("survey_last_person_count") is not None else "",
        "stoarama_survey_vehicles_current": item.get("survey_last_vehicle_count") if item.get("survey_last_vehicle_count") is not None else "",
        "stoarama_survey_sampled_at": item.get("survey_last_sampled_at") or "", "stoarama_import_source": metadata.get("import_source") if isinstance(metadata, dict) else "",
        "stoarama_import_list": metadata.get("list") if isinstance(metadata, dict) else "", "stoarama_import_row_number": metadata.get("row_number") if isinstance(metadata, dict) else "",
        "stoarama_import_valid": csv_values.get("valid") or "", "stoarama_import_why": csv_values.get("why") or "",
        "stoarama_import_verified_at": metadata.get("verified_at") if isinstance(metadata, dict) else "",
    })


def browser_spec(value: str) -> tuple[str, ...]:
    browser, _, profile = value.partition(":")
    return (browser, profile) if profile else (browser,)


def apply_oembed_metadata(row: dict, payload: dict, previous_error: str = "") -> None:
    """Populate the public metadata that YouTube's oEmbed endpoint exposes."""
    author = payload.get("author_name") or ""
    author_url = payload.get("author_url") or ""
    row.update({"youtube_metadata_status": "partial_oembed", "youtube_current_title": payload.get("title") or "",
                "youtube_channel": author, "youtube_channel_url": author_url, "youtube_uploader": author,
                "youtube_webpage_url_current": row.get("youtube_url") or "", "youtube_thumbnail": payload.get("thumbnail_url") or "",
                "youtube_error": previous_error[:1000]})


def add_youtube_oembed(row: dict, previous_error: str = "") -> bool:
    try:
        query = urllib.parse.urlencode({"url": row["youtube_url"], "format": "json"})
        request = urllib.request.Request(f"https://www.youtube.com/oembed?{query}", headers={"User-Agent": "social-mixing-metadata-audit/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            apply_oembed_metadata(row, json.load(response), previous_error)
        return True
    except Exception as error:
        row["youtube_error"] = (previous_error + " | oEmbed: " + str(error))[:1000]
        return False


def add_youtube(row: dict, browser: str | None, oembed_only: bool = False) -> None:
    blank_fields(row, YOUTUBE_FIELDS)
    row["youtube_metadata_checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    if not row.get("youtube_url"):
        row["youtube_metadata_status"] = "not_youtube"
        return
    if oembed_only:
        if not add_youtube_oembed(row):
            row["youtube_metadata_status"] = "error"
        return
    try:
        import yt_dlp
        options = {"quiet": True, "skip_download": True, "noplaylist": True,
                   "js_runtimes": {"deno": {}}, "remote_components": {"ejs:github"}}
        if browser:
            options["cookiesfrombrowser"] = browser_spec(browser)
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(row["youtube_url"], download=False)
        row.update({
            "youtube_metadata_status": "ok", "youtube_current_title": info.get("title") or "",
            "youtube_description": info.get("description") or "", "youtube_channel": info.get("channel") or "",
            "youtube_channel_id": info.get("channel_id") or "", "youtube_channel_url": info.get("channel_url") or "",
            "youtube_uploader": info.get("uploader") or "", "youtube_uploader_id": info.get("uploader_id") or "",
            "youtube_upload_date": info.get("upload_date") or "", "youtube_release_timestamp": info.get("release_timestamp") or info.get("timestamp") or "",
            "youtube_duration_seconds_current": info.get("duration") or "", "youtube_live_status": info.get("live_status") or "",
            "youtube_availability": info.get("availability") or "", "youtube_webpage_url_current": info.get("webpage_url") or "",
            "youtube_thumbnail": info.get("thumbnail") or "", "youtube_categories": json.dumps(info.get("categories") or [], ensure_ascii=False),
            "youtube_tags": json.dumps(info.get("tags") or [], ensure_ascii=False), "youtube_language": info.get("language") or "",
            "youtube_view_count": info.get("view_count") or "", "youtube_like_count": info.get("like_count") or "", "youtube_error": "",
        })
    except Exception as error:
        if not add_youtube_oembed(row, str(error)):
            row["youtube_metadata_status"] = "error"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stoarama-api", default="https://stoarama.com/api/v1/dashboard/streams")
    parser.add_argument("--skip-stoarama", action="store_true")
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--youtube-browser", default="chrome:Profile 1")
    parser.add_argument("--youtube-no-cookies", action="store_true",
                        help="Fetch only public metadata without reading a browser profile")
    parser.add_argument("--youtube-oembed-only", action="store_true",
                        help="Use YouTube's public oEmbed response; omits description, tags, and live details")
    args = parser.parse_args()
    rows, original_fields = read_csv(args.input)
    snapshot_at = datetime.now(timezone.utc).isoformat()
    records = {} if args.skip_stoarama else fetch_stoarama(args.stoarama_api)
    for row in rows:
        if not args.skip_stoarama: add_stoarama(row, records.get(str(row.get("stream_id") or "")), snapshot_at)
        else: blank_fields(row, STOARAMA_FIELDS + LOCATION_FIELDS)
        if not args.skip_youtube: add_youtube(row, None if args.youtube_no_cookies else args.youtube_browser,
                                              oembed_only=args.youtube_oembed_only)
        else: blank_fields(row, YOUTUBE_FIELDS)
    fields = original_fields + [field for field in STOARAMA_FIELDS + LOCATION_FIELDS + YOUTUBE_FIELDS if field not in original_fields]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_csv(args.output, rows, fields)
    print(json.dumps({"rows": len(rows), "stoarama_rows": 0 if args.skip_stoarama else len(records),
                      "youtube_attempted": 0 if args.skip_youtube else sum(bool(row.get("youtube_url")) for row in rows),
                      "output": str(args.output)}))


if __name__ == "__main__":
    main()
