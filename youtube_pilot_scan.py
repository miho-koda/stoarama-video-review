#!/usr/bin/env python3
"""Find best 150-second annotation windows in YouTube live DVR playlists."""

from __future__ import annotations

import argparse, csv, json, os, subprocess, tempfile, urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from scan import daylight_score, fixed_camera_score

ROOT = Path(__file__).resolve().parent
YTDLP = ROOT / ".venv312/bin/yt-dlp"
MODEL = ROOT.parent / "models/yolo26n.pt"


@dataclass
class Segment:
    at: datetime
    duration: float
    url: str


def resolve(url: str) -> dict:
    proc = subprocess.run(
        [str(YTDLP), "--dump-single-json", "--skip-download", "--no-warnings", url],
        check=True, capture_output=True, text=True,
    )
    return json.loads(proc.stdout)


def choose_format(info: dict) -> dict:
    choices = [f for f in info["formats"] if f.get("protocol") == "m3u8_native" and f.get("height")]
    choices = [f for f in choices if 480 <= int(f["height"]) <= 1080] or choices
    return min(choices, key=lambda f: abs(int(f["height"]) - 720))


def parse_playlist(text: str) -> list[Segment]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    current = None
    duration = None
    out = []
    for line in lines:
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            current = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif not line.startswith("#") and current is not None and duration is not None:
            out.append(Segment(current, duration, line))
            current += timedelta(seconds=duration)
            duration = None
    return out


def frame_for(seg: Segment, cache: dict[str, np.ndarray | None]) -> np.ndarray | None:
    if seg.url in cache:
        return cache[seg.url]
    try:
        req = urllib.request.Request(seg.url, headers={"user-agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
        with tempfile.NamedTemporaryFile(suffix=".ts") as tmp:
            tmp.write(data); tmp.flush()
            cap = cv2.VideoCapture(tmp.name)
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, count // 2))
            ok, frame = cap.read(); cap.release()
        cache[seg.url] = frame if ok else None
    except Exception:
        cache[seg.url] = None
    return cache[seg.url]


def detections(model, frame) -> dict:
    result = model.predict(frame, classes=[0, 2, 3, 5, 7], verbose=False)[0]
    heights, vehicles = [], 0
    for cls, box in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist()):
        if int(cls) == 0:
            heights.append(float(box[3] - box[1]))
        else:
            vehicles += 1
    qualifying = sum(h >= 60 for h in heights)
    return {"people": len(heights), "qualifying": qualifying, "vehicles": vehicles,
            "heights": heights, "daylight": daylight_score(frame)}


def evaluate_window(model, segments: list[Segment], cache: dict) -> dict | None:
    # One sample every ~10 seconds across exactly 150 seconds.
    chosen = segments[::max(1, round(10 / max(segments[0].duration, 1)))][:15]
    frames, stats = [], []
    for seg in chosen:
        frame = frame_for(seg, cache)
        if frame is None: continue
        frames.append(frame); stats.append(detections(model, frame))
    if len(stats) < 12: return None
    usable = [2 <= s["qualifying"] <= 30 for s in stats]
    all_heights = [h for s in stats for h in s["heights"]]
    size_fraction = sum(h >= 60 for h in all_heights) / len(all_heights) if all_heights else 0
    daylight_fraction = sum(s["daylight"] >= .48 for s in stats) / len(stats)
    fixed = fixed_camera_score(frames)
    people = [s["qualifying"] for s in stats]
    vehicles = sum(s["vehicles"] for s in stats)
    passed = (sum(usable) / len(usable) >= .80 and size_fraction >= .70 and
              daylight_fraction >= .80 and fixed >= .65 and sum(people) > vehicles)
    return {
        "passed": passed, "annotation_usable_fraction": sum(usable) / len(usable),
        "people_ge60_fraction": size_fraction, "daylight_fraction": daylight_fraction,
        "fixed_camera_score": fixed, "people_min": min(people), "people_median": float(np.median(people)),
        "people_max": max(people), "person_height_median_px": float(np.median(all_heights)) if all_heights else 0,
        "person_height_min_px": min(all_heights) if all_heights else 0,
        "person_height_max_px": max(all_heights) if all_heights else 0,
        "qualifying_people_total": sum(people), "vehicles_total": vehicles,
    }


def scan_one(model, row: dict, hours: int) -> dict | None:
    info = resolve(row["review_url"]); fmt = choose_format(info)
    req = urllib.request.Request(fmt["url"], headers=fmt.get("http_headers", {}))
    playlist = urllib.request.urlopen(req, timeout=60).read().decode()
    segments = parse_playlist(playlist)
    cutoff = segments[-1].at - timedelta(hours=hours)
    segments = [s for s in segments if s.at >= cutoff]
    cache = {}; candidates = []
    # Coarse windows every 2 minutes, then full 10-second evaluation.
    for i in range(0, len(segments), max(1, round(120 / segments[0].duration))):
        end = i
        while end < len(segments) and (segments[end].at - segments[i].at).total_seconds() < 150:
            end += 1
        if end >= len(segments): break
        metrics = evaluate_window(model, segments[i:end + 1], cache)
        if metrics and metrics["passed"]:
            score = (metrics["annotation_usable_fraction"] + metrics["people_ge60_fraction"] +
                     metrics["daylight_fraction"] + metrics["fixed_camera_score"] -
                     max(0, metrics["people_median"] - 12) / 30)
            candidates.append((score, i, end, metrics))
        if len(cache) > 600: cache.clear()
    if not candidates: return None
    _, i, end, metrics = max(candidates)
    start, finish = segments[i].at, segments[i].at + timedelta(seconds=150)
    live_end = segments[-1].at + timedelta(seconds=segments[-1].duration)
    return row | metrics | {
        "segment_start_utc": start.isoformat(), "segment_end_utc": finish.isoformat(),
        "duration_seconds": 150, "live_offset_start_seconds": round((start-live_end).total_seconds()),
        "live_offset_end_seconds": round((finish-live_end).total_seconds()),
        "youtube_status": info.get("live_status") or info.get("availability"),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(), "whole_video_usable": "unknown",
        "timestamp_stability": "relative_live" if info.get("is_live") else "stable_vod",
    }


def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--hours",type=int,default=4); a=p.parse_args()
    rows=list(csv.DictReader(a.input.open())); model=YOLO(str(MODEL)); accepted=[]
    for n,row in enumerate(rows,1):
        print(f"[{n}/{len(rows)}] {row['name']}",flush=True)
        try:
            result=scan_one(model,row,a.hours)
            if result: accepted.append(result); print("  PASS",flush=True)
            else: print("  no passing interval",flush=True)
        except Exception as exc: print(f"  ERROR {exc}",flush=True)
    fields=sorted({k for row in accepted for k in row})
    with a.output.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(accepted)
    print(f"accepted={len(accepted)} output={a.output}")

if __name__=="__main__": main()
