#!/usr/bin/env python3
"""Discover and screen public Stoarama video clips without retaining video."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
import urllib.request
import urllib.parse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

BASE = "https://stoarama.com"
PERSON_CLASS = 0
VEHICLE_CLASSES = {2, 3, 5, 7}  # car, motorcycle, bus, truck (COCO)


def api_json(path: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"content-type": "application/json", "user-agent": "social-mixing-research/1.0"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def youtube_review_url(url: str, start_seconds: float | None) -> str:
    """Return a stable watch link, adding a start time only when one is needed."""
    if start_seconds is None:
        return url
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "t"]
    query.append(("t", f"{max(0, math.floor(start_seconds))}s"))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def youtube_playability(watch_html: str) -> tuple[str, str]:
    """Extract YouTube's current player status and reason from a watch page."""
    status = re.search(r'"playabilityStatus":\{"status":"([^"]+)"', watch_html)
    reason = re.search(r'"reason":"([^"]+)"', watch_html)
    return (status.group(1) if status else "UNKNOWN", reason.group(1) if reason else "")


def fixed_camera_score(frames) -> float:
    """Score viewpoint stability using robust global optical flow.

    A fixed camera should keep the median background displacement near zero.
    This is a screening signal, not a calibration measurement.
    """
    import cv2
    import numpy as np
    if len(frames) < 2:
        return 0.0
    displacements = []
    previous = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
    for frame in frames[1:]:
        current = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        points = cv2.goodFeaturesToTrack(previous, 300, 0.01, 10)
        if points is None or len(points) < 20:
            return 0.0
        tracked, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
        valid = status.reshape(-1).astype(bool)
        if valid.sum() < 20:
            return 0.0
        delta = tracked[valid] - points[valid]
        displacements.append(float(np.median(np.linalg.norm(delta.reshape(-1, 2), axis=1))))
        previous = current
    median_px = float(np.median(displacements))
    return max(0.0, min(1.0, 1.0 - median_px / 6.0))


def write_rows(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def discover(output: Path, page_size: int, max_streams: int) -> None:
    rows: list[dict] = []
    offset = 0
    while len(rows) < max_streams:
        limit = min(page_size, max_streams - len(rows))
        payload = api_json(f"/api/v1/dashboard/streams?limit={limit}&offset={offset}")
        for item in payload.get("items", []):
            stream = item["stream"]
            if stream.get("capture_type") not in {"hls", "http_video", "youtube_watch"}:
                continue
            sid = int(stream["id"])
            source_page = stream.get("source_page_url") or stream.get("source_url") or ""
            public_score = 0.9 if "youtube.com" in source_page or "youtu.be" in source_page else 0.65
            rows.append({
                "stream_id": sid,
                "name": stream.get("name", ""),
                "city": stream.get("location_city", ""),
                "region": stream.get("location_region", ""),
                "country": stream.get("location_country", ""),
                "capture_type": stream.get("capture_type", ""),
                "source_page_url": source_page,
                "stream_review_url": f"{BASE}/streams/{sid}",
                "public_access_score": public_score,
                "public_access_status": "definitely_possible" if public_score >= 0.85 else "maybe_possible",
                "public_access_warning": "Confirm uploader/source terms before reuse",
                "location_validation_status": "unverified",
            })
        offset += limit
        if offset >= int(payload.get("total", 0)) or not payload.get("items"):
            break
    write_rows(output, rows)


@dataclass
class Clip:
    id: int
    start: datetime
    end: datetime
    download_url: str


def clips_for_stream(stream_id: int, limit: int = 200) -> list[Clip]:
    payload = api_json(f"/api/v1/streams/{stream_id}/clips?limit={limit}")
    clips = []
    for item in payload.get("items", []):
        if item.get("capture_status") != "success" or not item.get("download_url"):
            continue
        clips.append(Clip(
            int(item["id"]), parse_time(item["segment_start_at"]),
            parse_time(item["segment_end_at"]), item["download_url"],
        ))
    return sorted(clips, key=lambda clip: clip.start)


def contiguous_windows(clips: list[Clip], minimum_seconds: float, gap_seconds: float = 5) -> Iterable[list[Clip]]:
    run: list[Clip] = []
    for clip in clips:
        if run and (clip.start - run[-1].end).total_seconds() > gap_seconds:
            if (run[-1].end - run[0].start).total_seconds() >= minimum_seconds:
                yield run
            run = []
        run.append(clip)
    if run and (run[-1].end - run[0].start).total_seconds() >= minimum_seconds:
        yield run


def daylight_score(frame) -> float:
    """Conservative original-image daylight score; CLAHE is intentionally not used."""
    import cv2
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    mean_v = float(value.mean()) / 255
    dark = float((value < 45).mean())
    colour = float((saturation > 25).mean())
    return max(0.0, min(1.0, 0.65 * mean_v + 0.25 * (1 - dark) + 0.10 * colour))


def analyse_window(clips: list[Clip], model, sample_seconds: float, min_person_height: int) -> dict:
    import cv2
    samples = usable = daylight = persons = vehicles = 0
    with tempfile.TemporaryDirectory(prefix="stoarama-") as tmp:
        for clip in clips:
            target = Path(tmp) / f"{clip.id}.mp4"
            urllib.request.urlretrieve(clip.download_url, target)
            cap = cv2.VideoCapture(str(target))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            step = max(1, int(fps * sample_seconds))
            for index in range(0, frame_count, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = cap.read()
                if not ok:
                    continue
                samples += 1
                if daylight_score(frame) >= 0.48:
                    daylight += 1
                result = model.predict(frame, classes=[0, 2, 3, 5, 7], verbose=False)[0]
                sample_people = sample_vehicles = 0
                for cls, xyxy in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist()):
                    height = xyxy[3] - xyxy[1]
                    if int(cls) == PERSON_CLASS and height >= min_person_height:
                        sample_people += 1
                    elif int(cls) in VEHICLE_CLASSES:
                        sample_vehicles += 1
                persons += sample_people
                vehicles += sample_vehicles
                if sample_people > 0:
                    usable += 1
            cap.release()
    return {
        "sample_count": samples,
        "usable_person_fraction": usable / samples if samples else 0,
        "daylight_fraction": daylight / samples if samples else 0,
        "qualifying_person_detections": persons,
        "vehicle_detections": vehicles,
    }


def scan(catalog: Path, output: Path, max_streams: int, model_name: str) -> None:
    from ultralytics import YOLO
    model = YOLO(model_name)
    manifest: list[dict] = []
    for source in read_rows(catalog)[:max_streams]:
        sid = int(source["stream_id"])
        for window in contiguous_windows(clips_for_stream(sid), 150):
            metrics = analyse_window(window, model, 5, 60)
            duration = (window[-1].end - window[0].start).total_seconds()
            passed = (
                duration >= 150
                and metrics["usable_person_fraction"] >= 0.70
                and metrics["daylight_fraction"] >= 0.80
                and metrics["qualifying_person_detections"] > metrics["vehicle_detections"]
            )
            if not passed:
                continue
            manifest.append(source | metrics | {
                "segment_start_utc": window[0].start.isoformat(),
                "segment_end_utc": window[-1].end.isoformat(),
                "duration_seconds": round(duration, 3),
                "clip_ids": ";".join(str(clip.id) for clip in window),
                "camera_height_status": "unscored",
                "camera_height_note": "Depth V3 ground-plane calibration pending",
                "video_urls": "",
            })
    write_rows(output, manifest)


def refresh_links(manifest: Path, output: Path) -> None:
    rows = read_rows(manifest)
    for row in rows:
        ids = [int(value) for value in row.get("clip_ids", "").split(";") if value]
        payload = api_json("/api/v1/clips/download-prepare", {
            "stream_id": int(row["stream_id"]), "segment_ids": ids[:120],
        })
        row["video_urls"] = " ".join(item.get("download_url", "") for item in payload.get("items", []))
        row["video_links_expire_note"] = "Stoarama links expire approximately 10 minutes after refresh"
    write_rows(output, rows)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    discover_p = commands.add_parser("discover")
    discover_p.add_argument("--output", type=Path, required=True)
    discover_p.add_argument("--page-size", type=int, default=50)
    discover_p.add_argument("--max-streams", type=int, default=500)
    scan_p = commands.add_parser("scan")
    scan_p.add_argument("--catalog", type=Path, required=True)
    scan_p.add_argument("--output", type=Path, required=True)
    scan_p.add_argument("--max-streams", type=int, default=10)
    scan_p.add_argument("--model", default="yolo11x.pt")
    refresh_p = commands.add_parser("refresh-links")
    refresh_p.add_argument("--manifest", type=Path, required=True)
    refresh_p.add_argument("--output", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "discover":
        discover(args.output, args.page_size, args.max_streams)
    elif args.command == "scan":
        scan(args.catalog, args.output, args.max_streams, args.model)
    else:
        refresh_links(args.manifest, args.output)


if __name__ == "__main__":
    main()
