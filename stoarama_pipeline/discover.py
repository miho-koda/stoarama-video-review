from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .common import youtube_id


CATALOG_FIELDS = [
    "stream_id", "name", "capture_type", "source_url", "source_page_url",
    "source_key", "youtube_url", "video_id", "stoarama_url",
    "city", "region", "country", "country_code", "location_text", "timezone", "utc_offset_hours",
    "provider", "runtime_status", "recording_state", "captures_success",
    "survey_people", "survey_vehicles", "survey_sampled_at", "source_warning",
    "created_at", "updated_at",
]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "stoarama-pipeline/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def discover(api: str, source_types: list[str], max_records: int = 0) -> list[dict]:
    rows, offset, page_size = [], 0, 500
    while True:
        params = urllib.parse.urlencode({
            "limit": page_size, "offset": offset, "include_image_urls": "false",
            "capture_types": ",".join(source_types),
        })
        payload = fetch_json(f"{api}?{params}")
        batch = payload.get("items") or []
        for item in batch:
            stream = item.get("stream") or {}
            source_url = stream.get("source_url") or ""
            source_page = stream.get("source_page_url") or ""
            source = source_page or source_url
            vid = youtube_id(source_page) or youtube_id(source_url)
            capture_type = stream.get("capture_type") or (source_types[0] if len(source_types) == 1 else "")
            if capture_type == "youtube_watch" and not vid:
                continue
            source_key = f"youtube:{vid}" if vid else f"{capture_type}:{source_url or stream.get('id')}"
            metadata = stream.get("metadata_json") or {}
            csv_values = metadata.get("csv_values") if isinstance(metadata, dict) else {}
            source_warning = (csv_values or {}).get("why") or ""
            rows.append({
                "stream_id": stream.get("id"), "name": stream.get("name") or vid or str(stream.get("id")),
                "capture_type": capture_type, "source_url": source_url,
                "source_page_url": source_page, "source_key": source_key,
                "youtube_url": f"https://www.youtube.com/watch?v={vid}" if vid else "", "video_id": vid,
                "stoarama_url": f"https://stoarama.com/streams/{stream.get('id')}",
                "city": stream.get("location_city") or "", "region": stream.get("location_region") or "",
                "country": stream.get("location_country") or "", "country_code": stream.get("location_country_code") or "",
                "location_text": stream.get("location_text") or "", "timezone": "", "utc_offset_hours": "",
                "provider": stream.get("provider") or "",
                "runtime_status": stream.get("capture_runtime_status") or "",
                "recording_state": stream.get("recording_state") or "",
                "captures_success": item.get("captures_success") or 0,
                "survey_people": item.get("survey_last_person_count") if item.get("survey_last_person_count") is not None else "",
                "survey_vehicles": item.get("survey_last_vehicle_count") if item.get("survey_last_vehicle_count") is not None else "",
                "survey_sampled_at": item.get("survey_last_sampled_at") or "",
                "source_warning": source_warning,
                "created_at": stream.get("created_at") or "", "updated_at": stream.get("updated_at") or "",
            })
            if max_records and len(rows) >= max_records:
                break
        if (max_records and len(rows) >= max_records) or not batch or offset + len(batch) >= int(payload.get("total") or 0):
            break
        offset += len(batch)
    # Preserve the first Stoarama record for duplicate canonical sources.
    unique = {}
    for row in rows:
        unique.setdefault(row["source_key"], row)
    return list(unique.values())
