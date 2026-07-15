from __future__ import annotations

import os
import urllib.parse
from datetime import timedelta
from pathlib import Path

from .common import duration_for_score, read_csv, write_csv, youtube_id


SELECTION_FIELDS = [
    "row_id", "stream_id", "name", "video_id", "youtube_url", "stoarama_url",
    "city", "region", "country", "location_text", "timezone", "utc_offset_hours",
    "segment_start_utc", "segment_end_utc",
    "duration_seconds", "score", "people_min", "people_median", "people_max",
    "people_ge60_fraction", "daylight_fraction", "fixed_camera_score",
    "social_pair_score", "active_density_fraction", "vehicles_total", "status",
]


def select(catalog_path: str | Path, output_path: str | Path, rejected_path: str | Path,
           config: dict, model_path: str, target: int, max_videos: int, device: str,
           resume: bool = True) -> tuple[list[dict], list[dict]]:
    # Heavy dependencies are deliberately isolated to the GPU selection stage.
    from ultralytics import YOLO
    import youtube_dvr_scan as engine

    engine.configure(config, device)
    model = YOLO(model_path)
    catalog = read_csv(catalog_path)
    accepted = read_csv(output_path) if resume and Path(output_path).exists() else []
    rejected = read_csv(rejected_path) if resume and Path(rejected_path).exists() else []
    finished = {row.get("video_id") for row in accepted + rejected}
    lookback = int(config["lookback_hours"])
    coarse = int(config["coarse_interval_minutes"])
    top = int(config["top_windows_per_video"])
    examined = 0
    for source in catalog:
        if len(accepted) >= target or (max_videos and examined >= max_videos):
            break
        vid = source.get("video_id") or youtube_id(source.get("youtube_url", ""))
        if not vid or vid in finished:
            continue
        examined += 1; finished.add(vid)
        row = dict(source)
        row["review_url"] = source.get("youtube_url") or f"https://www.youtube.com/watch?v={vid}"
        print(f"[{examined}] {row.get('name') or vid}", flush=True)
        try:
            candidates, _ = engine.rank_video(row, model, lookback, coarse, top)
            if not candidates:
                rejected.append({"video_id": vid, "name": row.get("name", ""), "reason": "no passing interval"})
            else:
                score, start, metrics = candidates[0]
                duration = duration_for_score(float(score), config)
                end = start + timedelta(seconds=duration)
                accepted.append({
                    "row_id": len(accepted) + 1, "stream_id": row.get("stream_id", ""),
                    "name": row.get("name") or vid, "video_id": vid,
                    "youtube_url": row["review_url"], "stoarama_url": row.get("stoarama_url", ""),
                    "city": row.get("city", ""), "region": row.get("region", ""),
                    "country": row.get("country", ""), "location_text": row.get("location_text", ""),
                    "timezone": row.get("timezone", ""), "utc_offset_hours": row.get("utc_offset_hours", ""),
                    "segment_start_utc": start.isoformat(), "segment_end_utc": end.isoformat(),
                    "duration_seconds": duration, "score": float(score), **metrics, "status": "selected",
                })
                print(f"  PASS {start.isoformat()} duration={duration}s score={float(score):.3f}", flush=True)
        except Exception as error:
            rejected.append({"video_id": vid, "name": row.get("name", ""), "reason": str(error)})
            print(f"  ERROR {error}", flush=True)
        write_csv(output_path, accepted, SELECTION_FIELDS)
        write_csv(rejected_path, rejected, ["video_id", "name", "reason"])
    return accepted, rejected
