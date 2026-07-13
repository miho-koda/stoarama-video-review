#!/usr/bin/env python3
"""Download the selected 10-video pilot on a Mac and optionally upload to Drive."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import yt_dlp
except ImportError:
    raise SystemExit("Install the Python package first: python3 -m pip install -U yt-dlp")


CLIPS = [
    {"row_id": 1, "name": "Main Street Livecam, Canmore", "video_id": "_0wPODlF9wU", "start_utc": "2026-07-10T21:04:08.566162+00:00", "duration": 150, "people": "5/9/11", "score": 5.529},
    {"row_id": 2, "name": "Best Pancake Man", "video_id": "e9T0L_POAOk", "start_utc": "2026-07-08T03:06:27.837638+00:00", "duration": 150, "people": "6/10/11", "score": 5.887},
    {"row_id": 3, "name": "Venice Beach North", "video_id": "98jOtUeM3m8", "start_utc": "2026-07-10T19:39:13.326035+00:00", "duration": 150, "people": "7/10/11", "score": 5.850},
    {"row_id": 4, "name": "Venice Beach South", "video_id": "D33ZD6sRvnA", "start_utc": "2026-07-09T22:12:37.824113+00:00", "duration": 150, "people": "8/12/14", "score": 5.949},
    {"row_id": 5, "name": "Henry Africa Bar", "video_id": "6MMXJrzT5c0", "start_utc": "2026-07-11T02:29:42.138118+00:00", "duration": 90, "people": "2/3/4", "score": 2.764},
    {"row_id": 6, "name": "Hush Bar", "video_id": "DwKCna1mumk", "start_utc": "2026-07-12T00:48:25.145163+00:00", "duration": 120, "people": "0/5/11", "score": 4.265},
    {"row_id": 7, "name": "Bondi Aussie Bar", "video_id": "VR-x3HdhKLQ", "start_utc": "2026-07-11T11:20:46.592362+00:00", "duration": 120, "people": "2/5/9", "score": 4.164},
    {"row_id": 8, "name": "El Gaucho Soi 11", "video_id": "UemFRPrl1hk", "start_utc": "2026-07-08T01:23:36.119984+00:00", "duration": 150, "people": "5/9/14", "score": 5.583},
    {"row_id": 9, "name": "SIN Punch Machine", "video_id": "UNbOvsRAx9U", "start_utc": "2026-07-10T00:26:18.471479+00:00", "duration": 90, "people": "0/2/5", "score": 2.930},
    {"row_id": 10, "name": "Main Street Livecam Canmore - interval 2", "video_id": "_0wPODlF9wU", "start_utc": "2026-07-08T18:34:08.566162+00:00", "duration": 120, "people": "3/7/10", "score": 4.879},
]


class DVR:
    def __init__(self, video_id: str, browser: str):
        options = {
            "quiet": True,
            "live_from_start": True,
            "skip_download": True,
            "js_runtimes": {"deno": {}},
            "remote_components": {"ejs:github"},
        }
        if browser != "none":
            options["cookiesfrombrowser"] = (browser,)
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = [f for f in info["formats"] if f.get("height") == 720 and callable(f.get("fragments"))]
        if not formats:
            raise RuntimeError("YouTube did not expose a 720p live-from-start format")
        self.fmt = formats[0]
        first = next(self.fmt["fragments"]({}))
        self.first_url = first["url"]
        self.current_seq = int(first["fragment_count"]) - 1
        self.segment_seconds = float(self.fmt.get("target_duration") or 5)
        self.live_utc = datetime.now(timezone.utc)
        self.headers = self.fmt.get("http_headers", {})

    def fragment_url(self, at: datetime) -> str:
        seconds_behind = (self.live_utc - at).total_seconds()
        if seconds_behind > 120 * 3600:
            raise RuntimeError("interval has expired from YouTube's ~120-hour fragment history")
        seq = self.current_seq - round(seconds_behind / self.segment_seconds)
        parsed = urllib.parse.urlsplit(self.first_url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(key, str(seq) if key == "sq" else value) for key, value in query]
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def filename(clip: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", clip["name"].lower()).strip("-")
    return f"{clip['row_id']:02d}_{slug}.mp4"


def load_clips(manifest: str | None) -> list[dict]:
    if not manifest:
        return [dict(clip) for clip in CLIPS]
    if manifest.startswith(("http://", "https://")):
        with urllib.request.urlopen(manifest, timeout=60) as response:
            text = response.read().decode("utf-8")
        rows = list(csv.DictReader(text.splitlines()))
    else:
        with Path(manifest).expanduser().open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    clips = []
    for index, row in enumerate(rows, 1):
        video_id = row.get("video_id") or urllib.parse.parse_qs(urllib.parse.urlsplit(row.get("youtube_url", "")).query).get("v", [""])[0]
        if not video_id:
            raise ValueError(f"manifest row {index} has no video_id")
        clips.append({
            **row, "row_id": int(row.get("row_id") or index), "video_id": video_id,
            "name": row.get("name") or video_id,
            "start_utc": row.get("segment_start_utc") or row.get("start_utc"),
            "duration": int(float(row.get("duration_seconds") or row.get("duration") or 0)),
            "score": float(row.get("score") or 0),
            "people": row.get("people") or "/".join(str(row.get(key, "")) for key in ("people_min", "people_median", "people_max")),
        })
    return clips


def run(command: list[str], **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def preserve(clip: dict, output_dir: Path, browser: str) -> Path:
    output = output_dir / filename(clip)
    if output.exists():
        print(f"  already exists: {output.name}")
        return output
    dvr = DVR(clip["video_id"], browser)
    start = datetime.fromisoformat(clip["start_utc"])
    with tempfile.TemporaryDirectory(prefix="youtube_pilot_") as temporary:
        temporary = Path(temporary)
        parts = []
        step = round(dvr.segment_seconds)
        for offset in range(0, clip["duration"], step):
            request = urllib.request.Request(dvr.fragment_url(start + timedelta(seconds=offset)), headers=dvr.headers)
            data = urllib.request.urlopen(request, timeout=30).read()
            part = temporary / f"part_{offset:04d}.mp4"
            part.write_bytes(data)
            parts.append(part)
        concat = temporary / "concat.txt"
        concat.write_text("".join(f"file '{part}'\n" for part in parts))
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts",
             "-f", "concat", "-safe", "0", "-i", str(concat), "-t", str(clip["duration"]),
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
             "-movflags", "+faststart", str(output)])
    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(output)], capture_output=True)
    actual = float(json.loads(probe.stdout)["format"]["duration"])
    if abs(actual - clip["duration"]) > 3:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"duration validation failed: expected {clip['duration']}s, got {actual:.1f}s")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="chrome", choices=["chrome", "safari", "firefox", "none"])
    parser.add_argument("--output", type=Path, default=Path.home() / "stoarama-pilot-clips")
    parser.add_argument("--manifest", help="Selection CSV path or HTTPS URL; omit to use the bundled ten-row pilot")
    parser.add_argument("--upload", action="store_true", help="Upload to the configured pilotdrive: rclone remote")
    parser.add_argument("--drive-remote", default="pilotdrive:")
    args = parser.parse_args()
    for command in ("ffmpeg", "ffprobe"):
        if not shutil.which(command):
            raise SystemExit(f"Missing {command}. Install it with: brew install ffmpeg")
    if args.upload and not shutil.which("rclone"):
        raise SystemExit("Missing rclone. Install it with: brew install rclone")
    args.output.mkdir(parents=True, exist_ok=True)
    clips = load_clips(args.manifest)
    manifest = []
    # Preserve oldest selections first because the DVR history is rolling.
    for position, clip in enumerate(sorted(clips, key=lambda x: x["start_utc"]), 1):
        print(f"[{position}/{len(clips)}] {clip['name']}")
        try:
            path = preserve(clip, args.output, args.browser)
            drive_url = ""
            if args.upload:
                destination = f"{args.drive_remote.rstrip(':')}:{path.name}"
                run(["rclone", "copyto", str(path), destination])
                link = run(["rclone", "link", destination], capture_output=True)
                drive_url = link.stdout.strip()
            start = datetime.fromisoformat(clip["start_utc"])
            start_local = ""; end_local = ""
            if clip.get("timezone"):
                zone = ZoneInfo(clip["timezone"])
                start_local = start.astimezone(zone).isoformat()
                end_local = (start + timedelta(seconds=clip["duration"])).astimezone(zone).isoformat()
            manifest.append({**clip, "filename": path.name,
                "youtube_url": f"https://www.youtube.com/watch?v={clip['video_id']}",
                "end_utc": (start + timedelta(seconds=clip["duration"])).isoformat(),
                "start_local": start_local, "end_local": end_local,
                "timestamp_accuracy": "approximately ±5 seconds",
                "drive_url": drive_url, "status": "downloaded"})
            print(f"  saved: {path.name}")
        except Exception as error:
            manifest.append({**clip, "filename": filename(clip),
                "youtube_url": f"https://www.youtube.com/watch?v={clip['video_id']}",
                "end_utc": "", "start_local": "", "end_local": "",
                "timestamp_accuracy": "approximately ±5 seconds",
                "drive_url": "", "status": f"ERROR: {error}"})
            print(f"  ERROR: {error}")
    manifest.sort(key=lambda row: row["row_id"])
    csv_path = args.output / "pilot_manifest.csv"
    with csv_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=manifest[0].keys())
        writer.writeheader(); writer.writerows(manifest)
    if args.upload:
        run(["rclone", "copyto", str(csv_path), f"{args.drive_remote.rstrip(':')}:pilot_manifest.csv"])
    successes = sum(row["status"] == "downloaded" for row in manifest)
    print(f"Finished: {successes}/{len(clips)} clips. Manifest: {csv_path}")
    if successes != len(clips):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
