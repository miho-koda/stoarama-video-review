#!/usr/bin/env python3
"""Resumable macOS side of the YouTube VOD/Drive/GPU exchange."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def run(command: list[str], **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def browser_spec(value: str):
    browser, _, profile = value.partition(":")
    return (browser, profile) if profile else (browser,)


def resolve(row: dict, browser: str) -> tuple[dict, dict]:
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("Install yt-dlp first: python3 -m pip install -U yt-dlp")
    options = {"quiet": True, "skip_download": True, "noplaylist": True,
               "js_runtimes": {"deno": {}}, "remote_components": {"ejs:github"},
               "cookiesfrombrowser": browser_spec(browser)}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(row["youtube_url"], download=False)
    formats = [item for item in info.get("formats", []) if item.get("url")
               and item.get("vcodec") != "none" and int(item.get("height") or 0) > 0
               and item.get("protocol") in {"http", "https", "m3u8", "m3u8_native"}]
    preferred = [item for item in formats if int(item.get("height") or 0) <= 720] or formats
    if not preferred:
        raise RuntimeError("no readable video format")
    hls = [item for item in preferred if str(item.get("protocol", "")).startswith("m3u8")]
    pool = hls or preferred
    return info, max(pool, key=lambda item: (int(item.get("height") or 0), float(item.get("tbr") or 0)))


def headers(fmt: dict) -> list[str]:
    values = fmt.get("http_headers") or {}
    return ["-headers", "".join(f"{key}: {value}\r\n" for key, value in values.items())] if values else []


def fetch_frame(fmt: dict, offset: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(offset),
         *headers(fmt), "-i", fmt["url"], "-frames:v", "1", "-q:v", "3", str(output)])


def fetch_clip(fmt: dict, offset: float, duration: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(offset),
         *headers(fmt), "-i", fmt["url"], "-t", str(duration), "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "25", "-movflags", "+faststart", str(output)])


def source_metadata(row: dict, info: dict) -> dict:
    release = info.get("release_timestamp") or info.get("timestamp")
    recording_start = datetime.fromtimestamp(release, timezone.utc).isoformat() if release else ""
    return {**row, "video_id": info.get("id") or row.get("video_id", ""),
            "youtube_title": info.get("title", ""), "video_duration_seconds": round(float(info.get("duration") or 0), 3),
            "recording_start_utc": recording_start,
            "recording_time_source": "youtube_release_timestamp" if release else "unknown",
            "recording_time_confidence": "approximate" if release else "unavailable"}


def copy_exchange(path: Path, remote: str, relative: str) -> None:
    run(["rclone", "copyto", str(path), f"{remote.rstrip('/')}" + "/" + relative])


def coarse(args) -> None:
    rows = read_csv(args.manifest)
    if args.video_ids:
        requested = set(args.video_ids.split(","))
        rows = [row for row in rows if row.get("video_id") in requested]
    rows = rows[:args.max_sources or None]
    ledger_path = args.work / "coarse_ledger.csv"
    ledger = read_csv(ledger_path) if ledger_path.exists() else []
    done = {row["source_key"] for row in ledger if row.get("status") == "complete"}
    manifest_path = args.work / "coarse_manifest.csv"
    output_rows = read_csv(manifest_path) if manifest_path.exists() else []
    for position, row in enumerate(rows, 1):
        if row["source_key"] in done:
            continue
        print(f"[{position}/{len(rows)}] {row.get('name')}", flush=True)
        try:
            info, fmt = resolve(row, args.browser)
            duration = float(info.get("duration") or 0)
            if duration < 180:
                raise RuntimeError(f"video too short: {duration:.1f}s")
            count = min(72, max(16, round(duration / 300)))
            base = args.work / "coarse" / row["video_id"]
            metadata = source_metadata(row, info)
            for index, offset in enumerate(np.linspace(75, max(75, duration - 75), count)):
                frame = base / f"{index:03d}_{round(float(offset), 3)}.jpg"
                if not frame.exists():
                    fetch_frame(fmt, float(offset), frame)
                output_rows.append({**metadata, "stage": "coarse", "sample_index": index,
                                    "offset_seconds": round(float(offset), 3),
                                    "relative_path": str(frame.relative_to(args.work))})
                copy_exchange(frame, args.remote, str(frame.relative_to(args.work)))
            ledger.append({"source_key": row["source_key"], "status": "complete", "error": ""})
        except Exception as error:
            ledger.append({"source_key": row["source_key"], "status": "error", "error": str(error)[:500]})
            print(f"  ERROR: {error}", flush=True)
        write_csv(manifest_path, output_rows); write_csv(ledger_path, ledger)
        copy_exchange(manifest_path, args.remote, manifest_path.name)
        copy_exchange(ledger_path, args.remote, ledger_path.name)


def candidates(args) -> None:
    shortlist = read_csv(args.input)
    grouped: dict[str, list[dict]] = {}
    for row in shortlist:
        grouped.setdefault(row["source_key"], []).append(row)
    output_path = args.work / "candidate_manifest.csv"
    output = read_csv(output_path) if output_path.exists() else []
    completed = {(row["source_key"], row["window_start_offset_seconds"], row["relative_path"]) for row in output}
    for source_key, choices in grouped.items():
        info, fmt = resolve(choices[0], args.browser)
        for choice in choices:
            start = float(choice["window_start_offset_seconds"])
            for sample_index, delta in enumerate(np.linspace(0, 150, 15, endpoint=False)):
                relative = Path("candidates") / choice["video_id"] / f"{round(start)}" / f"{sample_index:02d}.jpg"
                key = (source_key, choice["window_start_offset_seconds"], str(relative))
                if key in completed:
                    continue
                frame = args.work / relative
                fetch_frame(fmt, start + float(delta), frame)
                output.append({**choice, "stage": "candidate", "candidate_sample_index": sample_index,
                               "candidate_offset_seconds": round(start + float(delta), 3), "relative_path": str(relative)})
                copy_exchange(frame, args.remote, str(relative)); completed.add(key)
                write_csv(output_path, output)
        copy_exchange(output_path, args.remote, output_path.name)


def preserve(args) -> None:
    selections = read_csv(args.input)
    manifest_path = args.work / "final_manifest.csv"
    output = read_csv(manifest_path) if manifest_path.exists() else []
    done = {row["source_key"] for row in output if row.get("status") == "downloaded"}
    for row in selections:
        if row["source_key"] in done:
            continue
        try:
            info, fmt = resolve(row, args.browser)
            start = float(row["segment_start_offset_seconds"]); duration = int(float(row["duration_seconds"]))
            clip = args.work / "final_clips" / f"{row['video_id']}_{round(start)}_{duration}.mp4"
            if not clip.exists():
                fetch_clip(fmt, start, duration, clip)
            relative = str(clip.relative_to(args.work)); copy_exchange(clip, args.remote, relative)
            recording_start = row.get("recording_start_utc", "")
            start_utc = (datetime.fromisoformat(recording_start) + timedelta(seconds=start)).isoformat() if recording_start else ""
            end_utc = (datetime.fromisoformat(start_utc) + timedelta(seconds=duration)).isoformat() if start_utc else ""
            output.append({**row, "segment_start_utc": start_utc, "segment_end_utc": end_utc,
                           "relative_path": relative, "status": "downloaded"})
        except Exception as error:
            output.append({**row, "status": "error", "error": str(error)[:500]})
        write_csv(manifest_path, output); copy_exchange(manifest_path, args.remote, manifest_path.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["coarse", "candidates", "preserve"])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--work", type=Path, default=Path.home() / "stoarama-vod-exchange")
    parser.add_argument("--browser", default="chrome")
    parser.add_argument("--remote", default="pilotdrive:vod_exchange")
    parser.add_argument("--max-sources", type=int, default=0)
    parser.add_argument("--video-ids", default="", help="Comma-separated pilot subset for the coarse stage")
    args = parser.parse_args()
    for command in ("ffmpeg", "rclone"):
        if not shutil.which(command):
            raise SystemExit(f"Missing {command}")
    if args.stage == "coarse":
        if not args.manifest: parser.error("coarse requires --manifest")
        coarse(args)
    elif args.stage == "candidates":
        if not args.input: parser.error("candidates requires --input")
        candidates(args)
    else:
        if not args.input: parser.error("preserve requires --input")
        preserve(args)


if __name__ == "__main__":
    main()
