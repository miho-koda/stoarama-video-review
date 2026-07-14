#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import yt_dlp

from overnight_scan import ACCEPTED_FIELDS, LEDGER_FIELDS, SCANNER_REVISION, config_fingerprint, finalize, upload
from stoarama_pipeline.common import duration_for_score, load_config, read_csv, write_csv
from stoarama_pipeline.media import analyse_video, frame_metrics, trim_video

COOKIE_FILE = ""


def vod_ffmpeg_executable() -> str:
    configured = os.environ.get("STOARAMA_VOD_FFMPEG", "")
    if configured:
        return configured
    conda_ffmpeg = Path.home() / ".stoarama-ffmpeg" / "bin" / "ffmpeg"
    if conda_ffmpeg.is_file():
        return str(conda_ffmpeg)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70]


def resolve(row: dict) -> tuple[dict, dict]:
    options = {
        "quiet": True, "skip_download": True, "noplaylist": True,
        "js_runtimes": {"deno": {}}, "remote_components": {"ejs:github"},
    }
    if COOKIE_FILE:
        options["cookiefile"] = COOKIE_FILE
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(row["youtube_url"], download=False)
    duration = float(info.get("duration") or 0)
    if duration < 180:
        raise RuntimeError(f"VOD is too short: {duration:.1f}s")
    formats = [fmt for fmt in info.get("formats", []) if fmt.get("url") and fmt.get("vcodec") != "none"
               and fmt.get("protocol") in {"https", "http"} and int(fmt.get("height") or 0) > 0]
    preferred = [fmt for fmt in formats if int(fmt.get("height") or 0) <= 720] or formats
    if not preferred:
        raise RuntimeError("no directly readable VOD video format")
    fmt = max(preferred, key=lambda item: (int(item.get("height") or 0), float(item.get("tbr") or 0)))
    return info, fmt


def headers_argument(fmt: dict) -> list[str]:
    headers = fmt.get("http_headers") or {}
    if not headers:
        return []
    return ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]


def frame_at(fmt: dict, offset: float, target: Path) -> np.ndarray | None:
    command = [vod_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(offset),
               *headers_argument(fmt), "-i", fmt["url"], "-frames:v", "1", "-q:v", "3", str(target)]
    subprocess.run(command, check=True, timeout=75)
    return cv2.imread(str(target))


def download_window(fmt: dict, offset: float, duration: int, target: Path) -> None:
    subprocess.run([
        vod_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(offset),
        *headers_argument(fmt), "-i", fmt["url"], "-t", str(duration), "-an", "-c:v", "mpeg4",
        "-q:v", "5", "-movflags", "+faststart", str(target),
    ], check=True, timeout=duration + 150)


def scan_vod(row: dict, model, config: dict, device: str, clip_dir: Path) -> tuple[dict, Path] | None:
    info, fmt = resolve(row)
    total = float(info["duration"])
    window = 150
    sample_count = min(72, max(16, round(total / 300)))
    offsets = np.linspace(0, max(0, total - window), sample_count)
    ranked = []
    with tempfile.TemporaryDirectory(prefix="vod-recovery-") as temporary_raw:
        temporary = Path(temporary_raw)
        for index, offset in enumerate(offsets):
            try:
                frame = frame_at(fmt, float(offset + window / 2), temporary / f"coarse-{index}.jpg")
                if frame is None:
                    continue
                metrics = frame_metrics(model, frame, config, device)
                people = int(metrics["people"])
                if metrics["daylight"] < .46 or not 2 <= people <= int(config["qualifying_people_max"]):
                    continue
                score = metrics["daylight"] + min(metrics["pairs"], 8) / 8 - abs(people - 10) / 20
                ranked.append((score, float(offset)))
            except Exception as error:
                print(f"    coarse_error offset={offset:.1f} error={error}", flush=True)
        best = None
        for _, offset in sorted(ranked, reverse=True)[:8]:
            try:
                candidate = temporary / f"candidate-{round(offset)}.mp4"
                download_window(fmt, offset, window, candidate)
                metrics = analyse_video(candidate, model, config, device)
                if metrics and metrics["passed"] and (best is None or metrics["score"] > best[0]["score"]):
                    best = (metrics, offset, candidate.read_bytes())
            except Exception as error:
                print(f"    window_error offset={offset:.1f} error={error}", flush=True)
        if not best:
            return None
        metrics, offset, data = best
        duration = duration_for_score(float(metrics["score"]), config)
        source = temporary / "selected-source.mp4"
        source.write_bytes(data)
        output = clip_dir / f"youtube-vod-{row['video_id']}-{safe_name(row.get('name') or row['video_id'])}.mp4"
        trim_video(source, output, duration)
        result = {
            **metrics, "duration_seconds": duration,
            "segment_start_utc": "", "segment_end_utc": "",
            "segment_start_offset_seconds": round(offset, 3),
            "segment_end_offset_seconds": round(offset + duration, 3),
            "provenance": "youtube_vod", "stoarama_clip_id": "",
        }
        return result, output


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable fixed-camera YouTube VOD recovery scanner")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--config", default="pipeline_config.json")
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--drive-remote", default="pilotdrive:overnight_scan/vod")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--max-sources", type=int, default=0)
    parser.add_argument("--cookies", default="", help="Netscape cookie file for authenticated YouTube extraction")
    args = parser.parse_args()
    from ultralytics import YOLO
    global COOKIE_FILE
    COOKIE_FILE = args.cookies

    config = load_config(args.config)
    args.work.mkdir(parents=True, exist_ok=True)
    clip_dir = args.work / "clips"; clip_dir.mkdir(exist_ok=True)
    rows = read_csv(args.input)
    rows = [row for row in rows if int.from_bytes(hashlib.sha256(row["source_key"].encode()).digest()[:8], "big")
            % args.shard_count == args.shard_index]
    accepted_path, ledger_path = args.work / "selections_all.csv", args.work / "scan_ledger.csv"
    accepted = read_csv(accepted_path) if accepted_path.exists() else []
    ledger = read_csv(ledger_path) if ledger_path.exists() else []
    finished = {row["source_key"] for row in ledger}
    pending = [row for row in rows if row["source_key"] not in finished]
    if args.max_sources:
        pending = pending[:args.max_sources]
    model = YOLO(args.model)
    fingerprint = config_fingerprint(config)
    for position, row in enumerate(pending, 1):
        print(f"[{position}/{len(pending)}] {row.get('country')} | {row.get('name')}", flush=True)
        status, reason = "rejected", "no passing VOD interval"
        try:
            selected = scan_vod(row, model, config, args.device, clip_dir)
            if selected:
                result, path = selected
                drive_url = upload_status = link_status = ""
                try:
                    drive_url, upload_status, link_status = upload(path, args.drive_remote)
                except Exception as error:
                    upload_status = f"ERROR: {error}"
                accepted.append({
                    **{field: row.get(field, "") for field in ACCEPTED_FIELDS}, **result,
                    "row_id": len(accepted) + 1, "local_path": str(path), "drive_url": drive_url,
                    "upload_status": upload_status, "link_status": link_status, "status": "selected",
                })
                status, reason = "accepted", ""
                print(f"  PASS offset={result['segment_start_offset_seconds']} score={result['score']:.3f}", flush=True)
        except Exception as error:
            status, reason = "error", str(error)
            print(f"  ERROR {error}", flush=True)
        now = datetime.now(timezone.utc).isoformat()
        ledger.append({
            "source_key": row["source_key"], "stream_id": row.get("stream_id", ""), "name": row.get("name", ""),
            "capture_type": row.get("capture_type", "youtube_watch"), "country": row.get("country", ""),
            "status": status, "reason": reason, "source_link_status": "reachable" if status != "error" else "extraction_failure",
            "resolved_source_url": row.get("youtube_url", ""), "review_link_status": "verified" if status == "accepted" else "not_applicable",
            "link_failure_class": "" if status != "error" else "extraction_failure",
            "recommended_action": "" if status != "error" else "manual_review",
            "retry_count": 1, "last_checked_utc": now, "scanner_revision": SCANNER_REVISION,
            "config_fingerprint": fingerprint, "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
            "finished_at_utc": now,
        })
        write_csv(accepted_path, accepted, ACCEPTED_FIELDS)
        write_csv(ledger_path, ledger, LEDGER_FIELDS)
        if position % 10 == 0:
            finalize(accepted, args.work, config, args.drive_remote)
    finalize(accepted, args.work, config, args.drive_remote)
    summary = {"sources": len(rows), "processed": len(ledger), "accepted": len(accepted),
               "remaining": len(rows) - len({row['source_key'] for row in ledger})}
    (args.work / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
