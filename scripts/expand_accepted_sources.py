#!/usr/bin/env python3
"""Find up to four additional strict 120-second clips for each accepted source.

This is intentionally separate from the original overnight scan: it only reads
the 40 accepted parent rows, never rewrites their manifest, and uploads into a
separate Drive review destination.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _paths import PROJECT_ROOT
from overnight_scan import preserve_youtube, safe_name, upload
from stoarama_pipeline.common import load_config, read_csv, write_csv


EXPANSION_FIELDS = [
    "parent_row_id", "parent_source_key", "parent_drive_url", "parent_segment_start_utc",
    "parent_segment_end_utc", "expansion_rank", "run_revision", "candidate_start_utc",
    "remote_size_bytes", "local_media_deleted", "expansion_status",
]
LEDGER_FIELDS = [
    "source_key", "video_id", "parent_row_id", "status", "reason", "attempts",
    "strict_candidates", "non_overlapping_candidates", "uploaded_clips", "shortfall",
    "downloaded_bytes", "deleted_bytes", "updated_at", "run_revision",
]


def csv_fields(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def overlaps(start: datetime, duration_seconds: int, blocked_start: datetime, blocked_end: datetime) -> bool:
    """True for a real interval intersection; touching endpoints is allowed."""
    return start < blocked_end and blocked_start < start + timedelta(seconds=duration_seconds)


def choose_non_overlapping(candidates: list[tuple[float, datetime, dict]], blocked: list[tuple[datetime, datetime]],
                           duration_seconds: int, limit: int) -> list[tuple[float, datetime, dict]]:
    selected = []
    for candidate in sorted(candidates, key=lambda value: (-float(value[0]), value[1])):
        _, start, _ = candidate
        if any(overlaps(start, duration_seconds, other_start, other_end) for other_start, other_end in blocked):
            continue
        selected.append(candidate)
        blocked.append((start, start + timedelta(seconds=duration_seconds)))
        if len(selected) >= limit:
            break
    return selected


def shard_for(source_key: str, shard_count: int) -> int:
    return int.from_bytes(hashlib.sha256(source_key.encode()).digest()[:8], "big") % shard_count


def remote_size(path: Path, remote: str) -> int:
    rclone = os.path.expanduser("~/.local/bin/rclone")
    destination = f"{remote.rstrip('/')}/{path.name}"
    result = subprocess.run([rclone, "lsjson", destination], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    if not payload or "Size" not in payload[0]:
        raise RuntimeError("rclone did not return uploaded file size")
    size = int(payload[0]["Size"])
    if size != path.stat().st_size:
        raise RuntimeError(f"remote size mismatch: local={path.stat().st_size} remote={size}")
    return size


def expansion_row(parent: dict, score: float, start: datetime, metrics: dict, rank: int, duration: int,
                  run_revision: str, drive_url: str, upload_status: str, link_status: str,
                  remote_bytes: int, local_deleted: bool) -> dict:
    end = start + timedelta(seconds=duration)
    return {
        **parent,
        "parent_row_id": parent.get("row_id", ""), "parent_source_key": parent["source_key"],
        "parent_drive_url": parent.get("drive_url", ""),
        "parent_segment_start_utc": parent.get("segment_start_utc", ""),
        "parent_segment_end_utc": parent.get("segment_end_utc", ""),
        "expansion_rank": rank, "run_revision": run_revision,
        "candidate_start_utc": start.isoformat(), "segment_start_utc": start.isoformat(),
        "segment_end_utc": end.isoformat(), "duration_seconds": duration, "score": float(score),
        "provenance": "youtube_dvr_expansion", "drive_url": drive_url,
        "upload_status": upload_status, "link_status": link_status, "status": "review_candidate",
        "remote_size_bytes": remote_bytes, "local_media_deleted": str(local_deleted).lower(),
        "expansion_status": "uploaded_verified" if link_status == "verified" else "upload_unverified",
        **metrics,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", type=Path, required=True, help="canonical 40-row enriched CSV")
    result.add_argument("--work", type=Path, required=True)
    result.add_argument("--model", required=True)
    result.add_argument("--device", default="0")
    result.add_argument("--drive-remote", required=True)
    result.add_argument("--config", default=str(PROJECT_ROOT / "config" / "pipeline_config.json"))
    result.add_argument("--shard-count", type=int, default=1)
    result.add_argument("--shard-index", type=int, default=0)
    result.add_argument("--max-sources", type=int, default=0, help="bounded smoke-test only")
    result.add_argument("--hours", type=float, default=5.5)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("shard-index must be between 0 and shard-count - 1")
    from ultralytics import YOLO
    import youtube_dvr_scan as youtube_engine

    config = load_config(args.config)
    policy = config["expansion"]
    duration, cap = int(policy["duration_seconds"]), int(policy["clips_per_source"])
    run_revision = str(policy["run_revision"])
    fields = csv_fields(args.input)
    parents = read_csv(args.input)
    if len({row.get("source_key") for row in parents}) != len(parents):
        raise SystemExit("input must contain one accepted parent row per source_key")
    parents = [row for row in parents if shard_for(row["source_key"], args.shard_count) == args.shard_index]
    if args.max_sources:
        parents = parents[:args.max_sources]
    args.work.mkdir(parents=True, exist_ok=True)
    clips = args.work / "clips"; clips.mkdir(exist_ok=True)
    accepted_path, ledger_path = args.work / "expansion_selections.csv", args.work / "expansion_ledger.csv"
    accepted = read_csv(accepted_path) if accepted_path.exists() else []
    ledger = read_csv(ledger_path) if ledger_path.exists() else []
    completed = {row["source_key"] for row in ledger if row.get("status") == "complete"}
    fields = list(dict.fromkeys(fields + EXPANSION_FIELDS))
    model = YOLO(args.model)
    youtube_engine.configure(config, args.device)
    deadline = time.monotonic() + args.hours * 3600
    for parent in parents:
        if parent["source_key"] in completed or time.monotonic() >= deadline:
            continue
        prior = [row for row in accepted if row.get("parent_source_key") == parent["source_key"] and row.get("link_status") == "verified"]
        blocked = [(parse_utc(parent["segment_start_utc"]), parse_utc(parent["segment_end_utc"]))]
        blocked.extend((parse_utc(row["segment_start_utc"]), parse_utc(row["segment_end_utc"])) for row in prior)
        reason, strict_count, non_overlap_count, downloaded, deleted = "", 0, 0, 0, 0
        try:
            candidates, dvr = youtube_engine.rank_video(
                {**parent, "review_url": parent["youtube_url"]}, model,
                lookback_hours=int(policy["lookback_hours"]),
                coarse_minutes=int(policy["coarse_interval_minutes"]),
                top_windows=int(policy["top_windows_per_video"]), duration_seconds=duration,
            )
            strict_count = len(candidates)
            selections = choose_non_overlapping(candidates, blocked, duration, cap - len(prior))
            non_overlap_count = len(selections)
            for score, start, metrics in selections:
                name = f"youtube-{parent['video_id']}-{int(start.timestamp())}-{safe_name(parent.get('name') or parent['video_id'])}.mp4"
                local = preserve_youtube(dvr, parent, start, duration, clips, output_name=name)
                downloaded += local.stat().st_size
                drive_url, upload_status, link_status = upload(local, args.drive_remote)
                bytes_remote = remote_size(local, args.drive_remote) if link_status == "verified" else 0
                local_deleted = False
                if link_status == "verified" and bytes_remote:
                    size = local.stat().st_size
                    local.unlink()
                    deleted += size
                    local_deleted = True
                accepted.append(expansion_row(parent, score, start, metrics, len(prior) + 1, duration,
                                              run_revision, drive_url, upload_status, link_status,
                                              bytes_remote, local_deleted))
                if link_status == "verified":
                    prior.append(accepted[-1])
                else:
                    reason = "upload verification failed; local clip retained"
                    break
            if not reason and len(prior) < cap:
                reason = f"strict candidates={strict_count}; non_overlapping={non_overlap_count}; uploaded={len(prior)}"
            status = "complete"
        except Exception as error:
            status, reason = "error", str(error)
        ledger.append({
            "source_key": parent["source_key"], "video_id": parent.get("video_id", ""),
            "parent_row_id": parent.get("row_id", ""), "status": status, "reason": reason,
            "attempts": 1, "strict_candidates": strict_count, "non_overlapping_candidates": non_overlap_count,
            "uploaded_clips": len(prior), "shortfall": max(0, cap - len(prior)),
            "downloaded_bytes": downloaded, "deleted_bytes": deleted,
            "updated_at": datetime.now(timezone.utc).isoformat(), "run_revision": run_revision,
        })
        write_csv(accepted_path, accepted, fields)
        write_csv(ledger_path, ledger, LEDGER_FIELDS)
    summary = {
        "run_revision": run_revision, "shard_index": args.shard_index, "shard_count": args.shard_count,
        "assigned_sources": len(parents), "completed_sources": len({row["source_key"] for row in ledger if row.get("status") == "complete"}),
        "verified_clips": sum(row.get("link_status") == "verified" for row in accepted),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (args.work / "expansion_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
