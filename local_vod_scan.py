#!/usr/bin/env python3
"""Sequential, local-only YouTube VOD scanner for macOS.

Video is confined to ``WORK/current``.  Frames are decoded in memory, accepted
clips are removed only after a verified Drive copy, and the ledger is replaced
atomically after every source.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from scan import fixed_camera_score, youtube_review_url
from stoarama_pipeline.common import duration_for_score, load_config
from stoarama_pipeline.media import frame_metrics, probe_duration

GIB = 1024 ** 3
PROXY_CAP = 2 * GIB
WORK_CAP = 3 * GIB
PILOT_IDS = ("ElW4dUFEpuE", "3W0yKMCLiIs", "UwdghOblns0")
REVISION = "local-vod-scan-v4"
ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "manifests" / "vod_fixed_camera_priority.csv"

# Keep the established acceptance floor: it is resolution-specific, not a
# universal preference for close views. The pilot problem was that global
# aggregation hid the distribution of smaller detections, so we now report it.
LOCAL_MIN_PERSON_HEIGHT_PX = 60
LOCAL_PERSON_SIZE_FRACTION = .70
LOCAL_FIXED_CAMERA_MIN = .80
LOCAL_DENSE_STABILITY_MIN = .85
MAX_DENSE_TRANSLATION_PX = 1.25
MAX_DENSE_ROTATION_DEGREES = .20
MAX_DENSE_ZOOM_CHANGE_PERCENT = .40
MIN_CLIP_GAP_SECONDS = 300

REVIEW_FIELDS = [
    "source_key", "video_id", "name", "city", "region", "country", "location_text",
    "youtube_url", "youtube_review_url", "drive_path", "drive_link", "segment_start_offset_seconds",
    "segment_end_offset_seconds", "recording_start_utc", "segment_start_utc", "segment_end_utc",
    "recording_time_source", "recording_time_confidence", "duration_seconds", "score", "people_min",
    "people_median", "people_max", "people_ge60_fraction", "people_ge80_fraction", "size_frame_pass_fraction",
    "small_people_frame_fraction", "person_height_p25_px", "person_height_median_px", "daylight_fraction", "fixed_camera_score", "dense_stability_score",
    "camera_motion_median_px", "camera_motion_max_px", "camera_rotation_median_degrees",
    "camera_rotation_max_degrees", "camera_zoom_median_percent", "camera_zoom_max_percent",
    "social_pair_score", "active_density_fraction", "vehicles_total", "camera_assessment",
    "camera_height_confidence", "ptz_assessment", "public_access_warning", "run_revision",
]
LEDGER_FIELDS = [
    "source_key", "video_id", "name", "status", "attempts", "proxy_disposition",
    "candidate_disposition", "uploaded_clips", "downloaded_bytes", "deleted_bytes",
    "rejection_class", "reason", "updated_at", "run_revision",
]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def browser_spec(value: str) -> tuple[str, ...]:
    browser, _, profile = value.partition(":")
    return (browser, profile) if profile else (browser,)


def format_size(fmt: dict, duration: float) -> int | None:
    if fmt.get("filesize") or fmt.get("filesize_approx"):
        return int(fmt.get("filesize") or fmt.get("filesize_approx"))
    if fmt.get("tbr") and duration:
        return int(float(fmt["tbr"]) * 1000 / 8 * duration)
    return None


def select_proxy_format(formats: Iterable[dict], duration: float, cap: int = PROXY_CAP) -> dict:
    usable = [f for f in formats if f.get("vcodec") not in {None, "none"} and 144 <= int(f.get("height") or 0) <= 240]
    if not usable:
        raise ValueError("no_usable_144_240p_proxy")
    known = [(format_size(f, duration), f) for f in usable]
    known = [(size, f) for size, f in known if size is not None]
    if not known:
        raise ValueError("proxy_size_unknown")
    size, selected = min(known, key=lambda item: (item[0], int(item[1].get("height") or 0)))
    if size > cap:
        raise ValueError(f"proxy_over_2gb:{size}")
    return selected


def select_candidate_format(formats: Iterable[dict]) -> dict:
    usable = [f for f in formats if f.get("url") and f.get("vcodec") not in {None, "none"} and int(f.get("height") or 0) <= 720]
    if not usable:
        raise ValueError("no_usable_720p_format")
    return max(usable, key=lambda f: (int(f.get("height") or 0), float(f.get("tbr") or 0)))


def overlapping_windows(duration: float, length: int = 150, overlap: int = 30) -> list[tuple[float, float]]:
    if duration < length:
        return []
    starts = list(np.arange(0, duration - length + .001, length - overlap, dtype=float))
    final = duration - length
    if not starts or final - starts[-1] > 1:
        starts.append(final)
    return [(round(float(start), 3), float(length)) for start in starts]


def choose_non_overlapping(candidates: list[dict], limit: int = 2, minimum_gap: float = MIN_CLIP_GAP_SECONDS) -> list[dict]:
    selected = []
    for item in sorted(candidates, key=lambda row: float(row["score"]), reverse=True):
        start, end = float(item["start"]), float(item["start"]) + float(item["duration"])
        if all(end + minimum_gap <= float(old["start"]) or start >= float(old["start"]) + float(old["duration"]) + minimum_gap
               for old in selected):
            selected.append(item)
            if len(selected) == limit:
                break
    return selected


def derive_timestamps(info: dict, offset: float, duration: int) -> dict:
    stamp = info.get("release_timestamp") or info.get("timestamp")
    if not stamp:
        return {"recording_start_utc": "", "segment_start_utc": "", "segment_end_utc": "",
                "recording_time_source": "unknown", "recording_time_confidence": "unavailable"}
    base = datetime.fromtimestamp(float(stamp), timezone.utc)
    start = base + timedelta(seconds=offset)
    return {"recording_start_utc": base.isoformat(), "segment_start_utc": start.isoformat(),
            "segment_end_utc": (start + timedelta(seconds=duration)).isoformat(),
            "recording_time_source": "youtube_release_timestamp", "recording_time_confidence": "approximate"}


def classify_rejection(metrics: dict | None) -> str:
    if not metrics: return "decode_failure"
    if metrics.get("fixed_camera_score", 0) < LOCAL_FIXED_CAMERA_MIN or metrics.get("dense_stability_score", 0) < LOCAL_DENSE_STABILITY_MIN:
        return "ptz_or_moving_camera"
    if metrics.get("camera_assessment") == "obvious_high_view": return "obvious_high_camera"
    if metrics.get("daylight_fraction", 0) < .75: return "night_or_low_light"
    if metrics.get("people_max", 999) > 30: return "excessive_crowd"
    if metrics.get("people_ge60_fraction", 0) < LOCAL_PERSON_SIZE_FRACTION: return "undersized_people"
    if metrics.get("vehicles_total", 0) >= metrics.get("people_total", 0): return "traffic_dominant"
    return "quality_threshold"


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def resolve(row: dict, browser: str) -> dict:
    import yt_dlp
    options = {"quiet": True, "skip_download": True, "noplaylist": True, "js_runtimes": {"deno": {}},
               "remote_components": {"ejs:github"}, "cookiesfrombrowser": browser_spec(browser)}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(row["youtube_url"], download=False)


def download_proxy(row: dict, fmt: dict, browser: str, target: Path) -> int:
    import yt_dlp
    target.parent.mkdir(parents=True, exist_ok=True)
    options = {"format": str(fmt["format_id"]), "outtmpl": str(target), "continuedl": True, "nopart": False,
               "noplaylist": True, "quiet": True, "js_runtimes": {"deno": {}},
               "remote_components": {"ejs:github"}, "cookiesfrombrowser": browser_spec(browser)}
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([row["youtube_url"]])
    return target.stat().st_size


def frames_at(path: Path, start: float, duration: float, samples: int) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path)); frames = []
    for second in np.linspace(start, start + duration, samples, endpoint=False):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(second) * 1000)
        ok, frame = capture.read()
        if ok: frames.append(frame)
    capture.release()
    return frames


def affine_motion(transform: np.ndarray) -> dict:
    """Convert an affine transform into translation, rotation, and zoom."""
    a, b = float(transform[0, 0]), float(transform[1, 0])
    return {"translation_px": math.hypot(float(transform[0, 2]), float(transform[1, 2])),
            "rotation_degrees": math.degrees(math.atan2(b, a)),
            "zoom_change_percent": abs(math.hypot(a, b) - 1.0) * 100}


def stable_camera_pair(motion: dict) -> bool:
    return (motion["translation_px"] <= MAX_DENSE_TRANSLATION_PX
            and abs(motion["rotation_degrees"]) <= MAX_DENSE_ROTATION_DEGREES
            and motion["zoom_change_percent"] <= MAX_DENSE_ZOOM_CHANGE_PERCENT)


def camera_stability_metrics(path: Path, start: float, duration: float) -> dict:
    """Measure viewpoint motion in short, dense bursts across a candidate.

    The old 10-second samples could score a moving camera as fixed. Here each
    burst compares frames 0.5 seconds apart and estimates global affine motion
    with RANSAC, so foreground walkers are less likely to dominate the score.
    """
    capture = cv2.VideoCapture(str(path)); motions = []
    burst_starts = np.linspace(start + 8, start + max(8, duration - 10), 5)
    for burst in burst_starts:
        gray_frames = []
        for second in np.arange(float(burst), float(burst) + 2.0, .5):
            capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = capture.read()
            if not ok: continue
            scale = min(1.0, 480.0 / frame.shape[1])
            if scale != 1.0:
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            gray_frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        for previous, current in zip(gray_frames, gray_frames[1:]):
            points = cv2.goodFeaturesToTrack(previous, 400, .01, 8)
            if points is None or len(points) < 30: continue
            tracked, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
            valid = status.reshape(-1).astype(bool)
            if valid.sum() < 30: continue
            transform, inliers = cv2.estimateAffinePartial2D(points[valid], tracked[valid], method=cv2.RANSAC,
                                                               ransacReprojThreshold=1.5)
            if transform is None or inliers is None or int(inliers.sum()) < 20: continue
            motions.append(affine_motion(transform))
    capture.release()
    if len(motions) < 6:
        return {"dense_stability_score": 0.0, "camera_motion_median_px": float("inf"), "camera_motion_max_px": float("inf"),
                "camera_rotation_median_degrees": float("inf"), "camera_rotation_max_degrees": float("inf"),
                "camera_zoom_median_percent": float("inf"), "camera_zoom_max_percent": float("inf")}
    translations = [item["translation_px"] for item in motions]
    rotations = [abs(item["rotation_degrees"]) for item in motions]
    zooms = [item["zoom_change_percent"] for item in motions]
    stable = float(np.mean([stable_camera_pair(item) for item in motions]))
    return {"dense_stability_score": stable, "camera_motion_median_px": float(np.median(translations)),
            "camera_motion_max_px": float(np.max(translations)),
            "camera_rotation_median_degrees": float(np.median(rotations)), "camera_rotation_max_degrees": float(np.max(rotations)),
            "camera_zoom_median_percent": float(np.median(zooms)), "camera_zoom_max_percent": float(np.max(zooms))}


def perspective_assessment(stats: list[dict]) -> tuple[str, str]:
    # Without calibration this only rejects the clearest distant/high-angle cases.
    heights = [h for stat in stats for h in stat["all_heights"]]
    if len(heights) >= 8 and float(np.median(heights)) < 28 and float(np.percentile(heights, 90)) < 45:
        return "obvious_high_view", "heuristic"
    return "not_obviously_high", "heuristic"


def analyse_frames(frames: list[np.ndarray], model, config: dict, device: str, full: bool,
                   person_threshold: float = LOCAL_MIN_PERSON_HEIGHT_PX, stability: dict | None = None) -> dict | None:
    if len(frames) < (12 if full else 5): return None
    stats = [frame_metrics(model, frame, config, device) for frame in frames]
    counts = [s["people"] for s in stats]; heights = [h for s in stats for h in s["all_heights"]]
    usable = float(np.mean([2 <= count <= 30 for count in counts]))
    ge60 = float(np.mean([h >= 60 for h in heights])) if heights else 0
    ge80 = float(np.mean([h >= 80 for h in heights])) if heights else 0
    sized = float(np.mean([h >= person_threshold for h in heights])) if heights else 0
    per_frame_size = [float(np.mean([h >= person_threshold for h in item["all_heights"]])) if item["all_heights"] else 0.0
                      for item in stats]
    size_frame_pass = float(np.mean([value >= LOCAL_PERSON_SIZE_FRACTION for value in per_frame_size]))
    small_people_frames = float(np.mean([value < LOCAL_PERSON_SIZE_FRACTION for value in per_frame_size]))
    daylight = float(np.mean([s["daylight"] >= .52 for s in stats]))
    fixed = fixed_camera_score(frames); vehicles = sum(s["vehicles"] for s in stats); people_total = sum(counts)
    pairs = float(np.mean([min(s["pairs"], 8) / 8 for s in stats])); active = float(np.mean([5 <= c <= 22 for c in counts]))
    camera, confidence = perspective_assessment(stats)
    stability = stability or {"dense_stability_score": 1.0, "camera_motion_median_px": 0.0, "camera_motion_max_px": 0.0,
                              "camera_rotation_median_degrees": 0.0, "camera_rotation_max_degrees": 0.0,
                              "camera_zoom_median_percent": 0.0, "camera_zoom_max_percent": 0.0}
    score = 2 * active + pairs + daylight + sized + fixed - abs(float(np.median(counts)) - 12) / 20 - max(0, max(counts) - 25) / 10
    passed = (usable >= .8 and sized >= LOCAL_PERSON_SIZE_FRACTION and daylight >= .75
              and fixed >= LOCAL_FIXED_CAMERA_MIN and stability["dense_stability_score"] >= LOCAL_DENSE_STABILITY_MIN
              and people_total > vehicles and camera != "obvious_high_view")
    return {"passed": passed, "score": score, "people_min": min(counts), "people_median": float(np.median(counts)),
            "people_max": max(counts), "people_total": people_total, "people_ge60_fraction": ge60,
            "people_ge80_fraction": ge80, "size_frame_pass_fraction": size_frame_pass,
            "small_people_frame_fraction": small_people_frames, "person_height_p25_px": float(np.percentile(heights, 25)) if heights else 0.0,
            "person_height_median_px": float(np.median(heights)) if heights else 0.0,
            "daylight_fraction": daylight, "fixed_camera_score": fixed, "social_pair_score": pairs,
            "active_density_fraction": active, "vehicles_total": vehicles, "camera_assessment": camera,
            "camera_height_confidence": confidence, "ptz_assessment": "fixed" if fixed >= LOCAL_FIXED_CAMERA_MIN and stability["dense_stability_score"] >= LOCAL_DENSE_STABILITY_MIN else "moving_or_ptz",
            **stability}


def coarse_candidates(proxy: Path, duration: float, model, config: dict, device: str) -> list[dict]:
    ranked = []
    for start, length in overlapping_windows(duration):
        frames = frames_at(proxy, start, length, 8)
        if not frames: continue
        # Scale the established 60 px at 720p rule for the low-resolution proxy.
        threshold = max(16, LOCAL_MIN_PERSON_HEIGHT_PX * frames[0].shape[0] / 720)
        proxy_config = {**config, "min_person_height_px": threshold}
        metrics = analyse_frames(frames, model, proxy_config, device, full=False, person_threshold=threshold)
        if metrics and metrics["daylight_fraction"] >= .5 and 2 <= metrics["people_median"] <= 30:
            ranked.append({"start": start, "duration": length, **metrics})
    return sorted(ranked, key=lambda row: row["score"], reverse=True)[:6]


def headers(fmt: dict) -> list[str]:
    values = fmt.get("http_headers") or {}
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in values.items())] if values else []


def fetch_clip(fmt: dict, start: float, duration: int, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
         *headers(fmt), "-i", fmt["url"], "-t", str(duration), "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "25", "-movflags", "+faststart", str(target)])
    return target.stat().st_size


def verify_upload(local: Path, remote: str) -> str:
    result = run(["rclone", "lsjson", remote], capture_output=True)
    listing = json.loads(result.stdout)
    if not listing or int(listing[0].get("Size", -1)) != local.stat().st_size:
        raise UploadVerificationError("remote_size_verification_failed")
    link = run(["rclone", "link", remote], capture_output=True).stdout.strip()
    if not link.startswith("http"): raise UploadVerificationError("remote_link_verification_failed")
    return link


class UploadVerificationError(RuntimeError):
    """The local clip must remain available for a safe verification retry."""


def select_device() -> str:
    try:
        import torch
        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        return "cpu"


@dataclass
class Paths:
    work: Path
    @property
    def current(self): return self.work / "current"
    @property
    def review(self): return self.work / "review_manifest.csv"
    @property
    def ledger(self): return self.work / "scan_ledger.csv"
    @property
    def disk(self): return self.work / "disk_report.json"


class Scanner:
    def __init__(self, args):
        self.args = args; self.paths = Paths(args.work); self.paths.work.mkdir(parents=True, exist_ok=True)
        self.config = load_config(args.config); self.device = select_device(); self.model = None
        self.peak = self.downloaded = self.deleted = 0

    def update_peak(self):
        self.peak = max(self.peak, tree_bytes(self.paths.current))
        if self.peak > WORK_CAP: raise RuntimeError(f"working_footprint_over_3gb:{self.peak}")

    def cleanup(self, preserve_parts=False) -> int:
        before = tree_bytes(self.paths.current)
        parts = {}
        if preserve_parts and self.paths.current.exists():
            for part in self.paths.current.rglob("*.part"): parts[part.name] = part.read_bytes()
        shutil.rmtree(self.paths.current, ignore_errors=True)
        if parts:
            self.paths.current.mkdir(parents=True, exist_ok=True)
            for name, data in parts.items(): (self.paths.current / name).write_bytes(data)
        after = tree_bytes(self.paths.current); self.deleted += before - after
        return before - after

    def process(self, row: dict) -> tuple[list[dict], dict]:
        info = resolve(row, self.args.browser); duration = float(info.get("duration") or 0)
        if duration < 180: raise ValueError("video_too_short")
        proxy_fmt = select_proxy_format(info.get("formats", []), duration)
        proxy = self.paths.current / f"{row['video_id']}.proxy.mp4"
        got = download_proxy(row, proxy_fmt, self.args.browser, proxy); self.downloaded += got; self.update_peak()
        shortlist = coarse_candidates(proxy, duration, self.model, self.config, self.device)
        if not shortlist: raise ValueError("no_promising_windows")
        fmt = select_candidate_format(info.get("formats", [])); passing = []; last_rejection = "quality_threshold"
        for index, coarse in enumerate(shortlist):
            candidate = self.paths.current / f"candidate-{index}.mp4"
            got = fetch_clip(fmt, coarse["start"], 150, candidate); self.downloaded += got; self.update_peak()
            stability = camera_stability_metrics(candidate, 0, 150)
            metrics = analyse_frames(frames_at(candidate, 0, 150, 15), self.model, self.config, self.device,
                                     full=True, stability=stability)
            if metrics and metrics["passed"]:
                passing.append({"start": coarse["start"], "duration": duration_for_score(metrics["score"], self.config), **metrics, "path": candidate})
            else:
                last_rejection = classify_rejection(metrics); self.deleted += candidate.stat().st_size if candidate.exists() else 0; candidate.unlink(missing_ok=True)
        chosen = choose_non_overlapping(passing)
        if not chosen: raise ValueError(last_rejection)
        review = []
        for position, item in enumerate(chosen, 1):
            clip = item["path"]; wanted = int(item["duration"])
            # Re-encode selected duration from the already fetched 150-second candidate.
            final = self.paths.current / f"final-{position}-{wanted}.mp4"
            run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip), "-t", str(wanted),
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-movflags", "+faststart", str(final)])
            if abs(probe_duration(final) - wanted) > 3: raise RuntimeError("local_duration_verification_failed")
            remote = f"{self.args.remote.rstrip('/')}/{row['video_id']}_{round(item['start'])}_{wanted}.mp4"
            run(["rclone", "copyto", str(final), remote]); link = verify_upload(final, remote)
            timestamps = derive_timestamps(info, float(item["start"]), wanted)
            review.append({**row, **item, **timestamps, "youtube_review_url": youtube_review_url(row["youtube_url"], item["start"]),
                "drive_path": remote, "drive_link": link, "segment_start_offset_seconds": item["start"],
                "segment_end_offset_seconds": item["start"] + wanted, "duration_seconds": wanted,
                "public_access_warning": "Confirm uploader/source terms and public accessibility before reuse",
                "run_revision": REVISION})
            self.deleted += final.stat().st_size; final.unlink()
        return review, {"rejection_class": "", "reason": ""}

    def execute(self, rows: list[dict]) -> None:
        from ultralytics import YOLO
        self.model = YOLO(str(self.args.model))
        ledger = read_csv(self.paths.ledger) if self.paths.ledger.exists() else []
        reviews = read_csv(self.paths.review) if self.paths.review.exists() else []
        latest = {row["source_key"]: row for row in ledger}
        # A run ledger is an inventory as well as a checkpoint: sources not yet
        # reached are explicit pending rows rather than silently absent.
        for source in rows:
            if source["source_key"] not in latest:
                pending = {**source, "status": "pending", "attempts": 0, "proxy_disposition": "not_started",
                    "candidate_disposition": "not_started", "uploaded_clips": 0, "downloaded_bytes": 0,
                    "deleted_bytes": 0, "rejection_class": "", "reason": "", "updated_at": "", "run_revision": REVISION}
                ledger.append(pending); latest[source["source_key"]] = pending
        atomic_csv(self.paths.ledger, ledger, LEDGER_FIELDS)
        for position, row in enumerate(rows, 1):
            if latest.get(row["source_key"], {}).get("status") == "complete": continue
            print(f"[{position}/{len(rows)}] {row['name']}", flush=True); self.cleanup()
            attempt = int(latest.get(row["source_key"], {}).get("attempts") or 0) + 1
            before_down, before_deleted = self.downloaded, self.deleted
            upload_failure = False
            try:
                new_reviews, disposition = self.process(row); reviews.extend(new_reviews); status = "complete"
                self.cleanup(); reason = disposition["reason"]; rejection = disposition["rejection_class"]
            except KeyboardInterrupt:
                self.cleanup(preserve_parts=True); self.write_report(); raise
            except Exception as error:
                reason = str(error)[:500]; rejection = reason.split(":", 1)[0]; status = "rejected" if isinstance(error, ValueError) else "error"
                upload_failure = isinstance(error, UploadVerificationError)
                if not upload_failure: self.cleanup()
            entry = {**row, "status": status, "attempts": attempt, "proxy_disposition": "deleted",
                "candidate_disposition": "retained_for_upload_verification" if upload_failure else "deleted",
                "uploaded_clips": sum(r["source_key"] == row["source_key"] for r in reviews),
                "downloaded_bytes": self.downloaded - before_down, "deleted_bytes": self.deleted - before_deleted,
                "rejection_class": rejection, "reason": reason, "updated_at": datetime.now(timezone.utc).isoformat(), "run_revision": REVISION}
            ledger = [old for old in ledger if old["source_key"] != row["source_key"]] + [entry]; latest[row["source_key"]] = entry
            atomic_csv(self.paths.review, reviews, REVIEW_FIELDS); atomic_csv(self.paths.ledger, ledger, LEDGER_FIELDS); self.write_report()
            if upload_failure:
                print("Stopping: local clip retained because Drive verification failed.", file=sys.stderr)
                break
        self.write_report()

    def write_report(self):
        report = {"peak_temporary_bytes": self.peak, "peak_below_3gb": self.peak <= WORK_CAP,
                  "downloaded_bytes": self.downloaded, "deleted_bytes": self.deleted,
                  "temporary_media_remaining": [str(p) for p in self.paths.current.rglob("*") if p.is_file()] if self.paths.current.exists() else [],
                  "no_temporary_media_remains": not self.paths.current.exists() or not any(self.paths.current.rglob("*")), "run_revision": REVISION}
        temp = self.paths.disk.with_suffix(".json.tmp"); temp.write_text(json.dumps(report, indent=2) + "\n"); os.replace(temp, self.paths.disk)


def doctor(args) -> int:
    checks = []
    def add(name, ok, detail): checks.append((name, bool(ok), detail))
    add("Python 3.11+", sys.version_info >= (3, 11), platform.python_version())
    for command in ("ffmpeg", "ffprobe", "yt-dlp", "deno", "rclone"): add(command, shutil.which(command), shutil.which(command) or "missing")
    try:
        import yt_dlp; add("yt-dlp Python package", True, yt_dlp.version.__version__)
    except Exception as error: add("yt-dlp Python package", False, str(error))
    add("model", args.model.is_file(), str(args.model))
    device = select_device(); add("compute", True, device)
    profile = Path.home() / "Library/Application Support/Google/Chrome/Profile 1"
    add("Chrome Profile 1 access", os.access(profile, os.R_OK), str(profile))
    free = shutil.disk_usage(args.work.parent if args.work.parent.exists() else Path.home()).free
    add("disk >=3 GiB", free >= WORK_CAP, f"{free / GIB:.1f} GiB free")
    remote_ok = False; detail = ""
    try:
        result = run(["rclone", "lsd", args.remote], capture_output=True, timeout=30); remote_ok = result.returncode == 0; detail = "readable"
    except Exception as error: detail = str(error)
    add("Drive access (read-only)", remote_ok, detail)
    for name, ok, detail in checks: print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    print("Doctor performs metadata-free, non-mutating checks; it downloads no video.")
    return 0 if all(ok for _, ok, _ in checks) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=["doctor", "pilot", "run"])
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    result.add_argument("--model", type=Path, default=ROOT / "yolo26n.pt")
    result.add_argument("--config", type=Path, default=ROOT / "pipeline_config.json")
    result.add_argument("--browser", default="chrome:Profile 1")
    result.add_argument("--remote", default="pilotdrive:local_vod_review")
    result.add_argument("--work", type=Path, default=Path.home() / "stoarama-local-vod-scan")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "doctor": return doctor(args)
    rows = read_csv(args.manifest)
    if args.command == "pilot":
        by_id = {row["video_id"]: row for row in rows}; rows = [by_id[video_id] for video_id in PILOT_IDS]
    Scanner(args).execute(rows); return 0


if __name__ == "__main__": raise SystemExit(main())
