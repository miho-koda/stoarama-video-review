from __future__ import annotations

import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2

from .media import analyse_video, frame_metrics, record_live, trim_video


BASE = "https://stoarama.com"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "stoarama-pipeline/0.2"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "stoarama-pipeline/0.2"})
    with urllib.request.urlopen(request, timeout=90) as response, path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def known_unsuitable(row: dict) -> str:
    warning = str(row.get("source_warning") or "").lower()
    blocked = ("predominantly car", "too high above", "moving camera", "ptz")
    return next((phrase for phrase in blocked if phrase in warning), "")


def _spread(values: list, limit: int) -> list:
    if len(values) <= limit:
        return values
    if limit <= 1:
        return [values[len(values) // 2]]
    return [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]


def archive_items(row: dict, config: dict) -> list[dict]:
    stream_id = int(row["stream_id"])
    availability = fetch_json(f"{BASE}/api/v1/streams/{stream_id}/clips/availability")
    days = [item["day"] for item in availability.get("days", [])[:int(config["archive_days_to_sample"])]]
    if not days:
        return []
    chosen_days = _spread(days, min(3, len(days)))
    buckets = []
    for day in chosen_days:
        payload = fetch_json(f"{BASE}/api/v1/streams/{stream_id}/clips/availability?day={day}")
        for bucket in payload.get("hour_buckets", []):
            if int(bucket.get("clip_count") or 0) > 0:
                buckets.append(bucket["hour_start"])
    buckets = _spread(buckets, int(config["archive_hours_per_stream"]))
    items = []
    for raw_start in buckets:
        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        end = start + timedelta(hours=1)
        query = urllib.parse.urlencode({
            "limit": 3, "captured_from": start.isoformat(), "captured_to": end.isoformat(),
        })
        payload = fetch_json(f"{BASE}/api/v1/streams/{stream_id}/clips?{query}")
        candidates = payload.get("items") or []
        if candidates:
            items.append(candidates[len(candidates) // 2])
    return items


def coarse_archive(item: dict, model, config: dict, device: str, temporary: Path) -> tuple[float, dict] | None:
    url = item.get("thumbnail_download_url")
    if not url:
        return None
    target = temporary / f"thumb-{item['id']}.jpg"
    download(url, target)
    frame = cv2.imread(str(target))
    if frame is None:
        return None
    metrics = frame_metrics(model, frame, config, device)
    people = metrics["people"]
    if metrics["daylight"] < .46 or not 1 <= people <= int(config["qualifying_people_max"]):
        return None
    score = metrics["daylight"] + min(metrics["pairs"], 8) / 8 - abs(people - 12) / 20
    return score, item


def rank_archive(row: dict, model, config: dict, device: str, clip_dir: Path) -> tuple[dict, Path] | None:
    with tempfile.TemporaryDirectory(prefix="stoarama-archive-") as raw_temporary:
        temporary = Path(raw_temporary)
        ranked = []
        for item in archive_items(row, config):
            try:
                result = coarse_archive(item, model, config, device, temporary)
                if result:
                    ranked.append(result)
            except Exception as error:
                print(f"    archive_thumbnail_error id={item.get('id')} error={error}", flush=True)
        best = None
        for _, item in sorted(ranked, reverse=True, key=lambda value: value[0])[:int(config["archive_full_windows"])]:
            try:
                raw = temporary / f"clip-{item['id']}.mp4"
                download(item["download_url"], raw)
                metrics = analyse_video(raw, model, config, device)
                if metrics and metrics["passed"] and (best is None or metrics["score"] > best[0]["score"]):
                    best = (metrics, item, raw.read_bytes())
            except Exception as error:
                print(f"    archive_clip_error id={item.get('id')} error={error}", flush=True)
        if not best:
            return None
        metrics, item, data = best
        duration = 90
        slug = re.sub(r"[^a-z0-9]+", "-", str(row.get("name") or row["stream_id"]).lower()).strip("-")[:70]
        source = temporary / "selected-source.mp4"
        source.write_bytes(data)
        output = clip_dir / f"stoarama-{row['stream_id']}-{item['id']}-{slug}.mp4"
        trim_video(source, output, duration)
        start = datetime.fromisoformat(item["segment_start_at"].replace("Z", "+00:00"))
        return ({**metrics, "segment_start_utc": start.isoformat(),
                 "segment_end_utc": (start + timedelta(seconds=duration)).isoformat(),
                 "duration_seconds": duration, "stoarama_clip_id": item["id"],
                 "provenance": "stoarama_archive"}, output)


def live_allowed(row: dict) -> bool:
    if row.get("capture_type") == "http_video":
        path = urllib.parse.urlsplit(row.get("source_url") or "").path.lower()
        if not path.endswith((".mp4", ".m3u8", ".mkv", ".webm")):
            return False
    people = str(row.get("survey_people") or "").strip()
    vehicles = str(row.get("survey_vehicles") or "").strip()
    if not people:
        return True
    return 1 <= int(float(people)) <= 30 and int(float(people)) > int(float(vehicles or 0))


def rank_live(row: dict, model, config: dict, device: str, clip_dir: Path) -> tuple[dict, Path] | None:
    if not config.get("live_fallback") or not live_allowed(row):
        return None
    url = row.get("source_url") or ""
    if not url:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", str(row.get("name") or row["stream_id"]).lower()).strip("-")[:70]
    with tempfile.TemporaryDirectory(prefix="stoarama-live-") as raw_temporary:
        probe = Path(raw_temporary) / "probe.mp4"
        record_live(url, probe, int(config["live_probe_seconds"]))
        probe_metrics = analyse_video(probe, model, config, device, samples=12)
        if not probe_metrics or not probe_metrics["passed"]:
            return None
        duration = 90
        output = clip_dir / f"stoarama-{row['stream_id']}-live-{slug}.mp4"
        started = datetime.now(timezone.utc)
        record_live(url, output, duration)
        metrics = analyse_video(output, model, config, device)
        if not metrics or not metrics["passed"]:
            output.unlink(missing_ok=True)
            return None
        return ({**metrics, "segment_start_utc": started.isoformat(),
                 "segment_end_utc": (started + timedelta(seconds=duration)).isoformat(),
                 "duration_seconds": duration, "stoarama_clip_id": "",
                 "provenance": "live_capture"}, output)
