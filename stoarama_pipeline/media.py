from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from scan import daylight_score, fixed_camera_score


VEHICLE_CLASSES = {2, 3, 5, 7}


def frame_metrics(model, frame, config: dict, device: str) -> dict:
    result = model.predict(frame, classes=[0, 2, 3, 5, 7], device=device, verbose=False)[0]
    people, vehicles = [], 0
    for cls, box in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist()):
        if int(cls) == 0:
            people.append((box, box[3] - box[1]))
        elif int(cls) in VEHICLE_CLASSES:
            vehicles += 1
    qualifying = [(box, height) for box, height in people if height >= float(config["min_person_height_px"])]
    pairs = 0
    for index, (first, first_height) in enumerate(qualifying):
        first_x, first_y = (first[0] + first[2]) / 2, first[3]
        for second, second_height in qualifying[index + 1:]:
            second_x, second_y = (second[0] + second[2]) / 2, second[3]
            scale = max(1, (first_height + second_height) / 2)
            distance = math.hypot(first_x - second_x, first_y - second_y) / scale
            if .45 <= distance <= 3 and abs(first_y - second_y) <= 1.2 * scale:
                pairs += 1
    return {
        "all_heights": [height for _, height in people], "people": len(qualifying),
        "vehicles": vehicles, "pairs": pairs, "daylight": daylight_score(frame),
    }


def analyse_video(path: str | Path, model, config: dict, device: str, samples: int = 15) -> dict | None:
    capture = cv2.VideoCapture(str(path))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    duration = frame_count / fps if frame_count else 0
    if frame_count < samples or duration < 12:
        capture.release()
        return None
    positions = np.linspace(0, max(0, frame_count - 1), samples).astype(int)
    frames, stats = [], []
    for position in positions:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(position))
        ok, frame = capture.read()
        if ok:
            frames.append(frame)
            stats.append(frame_metrics(model, frame, config, device))
    capture.release()
    if len(stats) < max(10, samples - 3):
        return None
    counts = [item["people"] for item in stats]
    heights = [height for item in stats for height in item["all_heights"]]
    usable = float(np.mean([int(config["qualifying_people_min"]) <= count <= int(config["qualifying_people_max"]) for count in counts]))
    sized = float(np.mean([height >= float(config["min_person_height_px"]) for height in heights])) if heights else 0
    daylight = float(np.mean([item["daylight"] >= .52 for item in stats]))
    fixed = fixed_camera_score(frames)
    vehicles = sum(item["vehicles"] for item in stats)
    pairs = float(np.mean([min(item["pairs"], 8) / 8 for item in stats]))
    median = float(np.median(counts))
    active = float(np.mean([5 <= count <= 22 for count in counts]))
    passed = (
        usable >= float(config["qualifying_frame_fraction"])
        and sized >= float(config["person_size_fraction"])
        and daylight >= float(config["daylight_frame_fraction"])
        and fixed >= float(config["fixed_camera_min_score"])
        and sum(counts) > vehicles
    )
    score = 2 * active + pairs + daylight + sized + fixed - abs(median - 12) / 20 - max(0, max(counts) - 25) / 10
    return {
        "passed": passed, "score": score, "people_min": min(counts),
        "people_median": median, "people_max": max(counts),
        "people_ge60_fraction": sized, "daylight_fraction": daylight,
        "fixed_camera_score": fixed, "social_pair_score": pairs,
        "active_density_fraction": active, "vehicles_total": vehicles,
        "decoded_duration_seconds": round(duration, 3),
    }


def ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg is not installed")


def probe_duration(path: str | Path) -> float:
    capture = cv2.VideoCapture(str(path))
    frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    capture.release()
    if not frames or not fps:
        raise RuntimeError("could not determine encoded video duration")
    return frames / fps


def trim_video(source: str | Path, destination: str | Path, duration: int) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-t", str(duration), "-an", "-c:v", "mpeg4", "-q:v", "5",
        "-movflags", "+faststart", str(destination),
    ], check=True)
    actual = probe_duration(destination)
    if abs(actual - duration) > 3:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"duration validation failed: expected {duration}s, got {actual:.1f}s")
    return destination


def record_live(url: str, destination: str | Path, duration: int) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-rw_timeout", "20000000",
        "-i", url, "-t", str(duration), "-an", "-c:v", "mpeg4", "-q:v", "5",
        "-movflags", "+faststart", str(destination),
    ], check=True, timeout=duration + 90)
    return destination
