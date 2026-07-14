#!/usr/bin/env python3
"""GPU ranking stages for frame packs fetched by mac_vod_exchange.py."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from scan import fixed_camera_score
from stoarama_pipeline.common import duration_for_score, load_config
from stoarama_pipeline.media import frame_metrics


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def coarse(args, model, config) -> None:
    ranked = defaultdict(list)
    for row in read_csv(args.input):
        frame = cv2.imread(str(args.exchange / row["relative_path"]))
        if frame is None:
            continue
        metrics = frame_metrics(model, frame, config, args.device)
        people = int(metrics["people"])
        heights = metrics["all_heights"]
        sized = float(np.mean([height >= float(config["min_person_height_px"]) for height in heights])) if heights else 0
        if metrics["daylight"] < .46 or not 2 <= people <= int(config["qualifying_people_max"]) or sized < .5:
            continue
        score = metrics["daylight"] + min(metrics["pairs"], 8) / 8 + sized - abs(people - 12) / 20
        ranked[row["source_key"]].append((score, row, metrics, sized))
    output = []
    for choices in ranked.values():
        for rank, (score, row, metrics, sized) in enumerate(sorted(choices, reverse=True, key=lambda item: item[0])[:8], 1):
            midpoint = float(row["offset_seconds"])
            output.append({**{key: value for key, value in row.items() if key not in {"relative_path", "sample_index", "stage"}},
                           "coarse_rank": rank, "coarse_score": round(score, 5),
                           "coarse_people": metrics["people"], "coarse_vehicles": metrics["vehicles"],
                           "coarse_pairs": metrics["pairs"], "coarse_daylight": round(metrics["daylight"], 5),
                           "coarse_people_ge60_fraction": round(sized, 5),
                           "window_start_offset_seconds": round(max(0, midpoint - 75), 3)})
    write_csv(args.output, output)


def final(args, model, config) -> None:
    groups = defaultdict(list)
    for row in read_csv(args.input):
        groups[(row["source_key"], row["window_start_offset_seconds"])].append(row)
    passing = defaultdict(list)
    for (source_key, start), rows in groups.items():
        rows.sort(key=lambda row: int(row["candidate_sample_index"]))
        frames, stats = [], []
        for row in rows:
            frame = cv2.imread(str(args.exchange / row["relative_path"]))
            if frame is not None:
                frames.append(frame); stats.append(frame_metrics(model, frame, config, args.device))
        if len(stats) < 12:
            continue
        counts = [item["people"] for item in stats]
        heights = [height for item in stats for height in item["all_heights"]]
        usable = float(np.mean([int(config["qualifying_people_min"]) <= count <= int(config["qualifying_people_max"]) for count in counts]))
        sized = float(np.mean([height >= float(config["min_person_height_px"]) for height in heights])) if heights else 0
        daylight = float(np.mean([item["daylight"] >= .52 for item in stats]))
        fixed = fixed_camera_score(frames)
        vehicles = sum(item["vehicles"] for item in stats); people_total = sum(counts)
        pairs = float(np.mean([min(item["pairs"], 8) / 8 for item in stats]))
        active = float(np.mean([5 <= count <= 22 for count in counts])); median = float(np.median(counts))
        passed = (usable >= float(config["qualifying_frame_fraction"])
                  and sized >= float(config["person_size_fraction"])
                  and daylight >= float(config["daylight_frame_fraction"])
                  and fixed >= float(config["fixed_camera_min_score"])
                  and people_total > vehicles)
        if not passed:
            continue
        score = 2 * active + pairs + daylight + sized + fixed - abs(median - 12) / 20 - max(0, max(counts) - 25) / 10
        passing[source_key].append((score, rows[0], {"people_min": min(counts), "people_median": median,
            "people_max": max(counts), "people_ge60_fraction": sized, "daylight_fraction": daylight,
            "fixed_camera_score": fixed, "social_pair_score": pairs, "active_density_fraction": active,
            "vehicles_total": vehicles}))
    output = []
    for choices in passing.values():
        score, row, metrics = max(choices, key=lambda item: item[0])
        duration = duration_for_score(score, config)
        output.append({**{key: value for key, value in row.items() if key not in {"relative_path", "candidate_sample_index", "candidate_offset_seconds", "stage"}},
                       **metrics, "score": round(score, 5), "duration_seconds": duration,
                       "segment_start_offset_seconds": row["window_start_offset_seconds"],
                       "segment_end_offset_seconds": round(float(row["window_start_offset_seconds"]) + duration, 3),
                       "provenance": "youtube_vod_mac_exchange"})
    write_csv(args.output, output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["coarse", "final"])
    parser.add_argument("--exchange", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", default="pipeline_config.json")
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    from ultralytics import YOLO
    model = YOLO(args.model); config = load_config(args.config)
    (coarse if args.stage == "coarse" else final)(args, model, config)


if __name__ == "__main__":
    main()
