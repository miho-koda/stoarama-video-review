from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .common import youtube_id


CATALOG_FIELDS = [
    "stream_id", "name", "youtube_url", "video_id", "stoarama_url",
    "city", "region", "country", "country_code", "location_text", "timezone", "utc_offset_hours",
    "provider", "runtime_status", "created_at", "updated_at",
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
            source = stream.get("source_page_url") or stream.get("source_url") or ""
            vid = youtube_id(source)
            if not vid:
                continue
            rows.append({
                "stream_id": stream.get("id"), "name": stream.get("name") or vid,
                "youtube_url": f"https://www.youtube.com/watch?v={vid}", "video_id": vid,
                "stoarama_url": f"https://stoarama.com/streams/{stream.get('id')}",
                "city": stream.get("location_city") or "", "region": stream.get("location_region") or "",
                "country": stream.get("location_country") or "", "country_code": stream.get("location_country_code") or "",
                "location_text": stream.get("location_text") or "", "timezone": "", "utc_offset_hours": "",
                "provider": stream.get("provider") or "",
                "runtime_status": stream.get("capture_runtime_status") or "",
                "created_at": stream.get("created_at") or "", "updated_at": stream.get("updated_at") or "",
            })
            if max_records and len(rows) >= max_records:
                break
        if (max_records and len(rows) >= max_records) or not batch or offset + len(batch) >= int(payload.get("total") or 0):
            break
        offset += len(batch)
    # Preserve the first Stoarama record for duplicate YouTube IDs.
    unique = {}
    for row in rows:
        unique.setdefault(row["video_id"], row)
    return list(unique.values())
