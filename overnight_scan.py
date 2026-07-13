#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stoarama_pipeline.common import duration_for_score, load_config, read_csv, write_csv
from stoarama_pipeline.discover import CATALOG_FIELDS, discover
from stoarama_pipeline.media import trim_video
from stoarama_pipeline.stoarama_sources import known_unsuitable, rank_archive, rank_live


ACCEPTED_FIELDS = [
    "row_id", "source_key", "stream_id", "name", "capture_type", "video_id",
    "youtube_url", "source_page_url", "source_url", "stoarama_url", "city", "region",
    "country", "location_text", "segment_start_utc", "segment_end_utc", "duration_seconds",
    "score", "people_min", "people_median", "people_max", "people_ge60_fraction",
    "daylight_fraction", "fixed_camera_score", "social_pair_score", "active_density_fraction",
    "vehicles_total", "provenance", "stoarama_clip_id", "local_path", "drive_url",
    "upload_status", "link_status", "status",
]
LEDGER_FIELDS = ["source_key", "stream_id", "name", "capture_type", "country", "status", "reason", "finished_at_utc"]


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70]


def round_robin(rows: list[dict]) -> list[dict]:
    groups: dict[str, deque] = defaultdict(deque)
    for row in sorted(rows, key=lambda item: (
            -int(float(item.get("captures_success") or 0) > 0),
            item.get("capture_type") == "youtube_watch", item.get("name") or "")):
        groups[row.get("country") or "Unknown"].append(row)
    ordered, countries = [], deque(sorted(groups, key=lambda key: (-len(groups[key]), key)))
    while countries:
        country = countries.popleft()
        ordered.append(groups[country].popleft())
        if groups[country]:
            countries.append(country)
    return ordered


def preserve_youtube(dvr, row: dict, start: datetime, duration: int, clip_dir: Path) -> Path:
    output = clip_dir / f"youtube-{row['video_id']}-{safe_name(row.get('name') or row['video_id'])}.mp4"
    with tempfile.TemporaryDirectory(prefix="youtube-preserve-") as raw_temporary:
        raw = Path(raw_temporary) / "raw.mp4"
        with raw.open("wb") as handle:
            handle.write(dvr.init_bytes)
            step = max(1, round(dvr.segment_seconds))
            for offset in range(0, duration + step, step):
                request = urllib.request.Request(
                    dvr.fragment_url(start + timedelta(seconds=offset)), headers=dvr.headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read()
                moof = data.find(b"moof")
                handle.write(data[max(0, moof - 4):] if moof >= 4 else data)
        trim_video(raw, output, duration)
    return output


def upload(path: Path, remote: str) -> tuple[str, str, str]:
    rclone = os.path.expanduser("~/.local/bin/rclone")
    destination = f"{remote.rstrip('/')}" + "/" + path.name
    subprocess.run([rclone, "copyto", str(path), destination], check=True)
    result = subprocess.run([rclone, "link", destination], check=True, capture_output=True, text=True)
    link = result.stdout.strip()
    if not link.startswith("https://"):
        raise RuntimeError("rclone did not return an HTTPS link")
    try:
        request = urllib.request.Request(link, headers={"User-Agent": "stoarama-pipeline/0.2"})
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 400:
                raise RuntimeError(f"review link returned HTTP {response.status}")
        link_status = "verified"
    except Exception as error:
        link_status = f"unverified: {error}"
    return link, "uploaded", link_status


def finalize(accepted: list[dict], work: Path, config: dict, remote: str) -> None:
    cap, target = int(config["country_review_cap"]), int(config["review_target"])
    counts, review = defaultdict(int), []
    for row in sorted(accepted, key=lambda item: float(item.get("score") or 0), reverse=True):
        country = row.get("country") or "Unknown"
        if counts[country] >= cap or not row.get("drive_url") or row.get("link_status") != "verified":
            continue
        counts[country] += 1
        review.append(dict(row))
        if len(review) >= target:
            break
    for index, row in enumerate(review, 1):
        row["row_id"] = index
    write_csv(work / "review_balanced.csv", review, ACCEPTED_FIELDS)
    write_csv(work / "selections_all.csv", accepted, ACCEPTED_FIELDS)
    needs_mac = [row for row in accepted if row.get("capture_type") == "youtube_watch" and not row.get("drive_url")]
    write_csv(work / "needs_mac_download.csv", needs_mac, ACCEPTED_FIELDS)
    rclone = os.path.expanduser("~/.local/bin/rclone")
    for name in ("review_balanced.csv", "selections_all.csv", "scan_ledger.csv", "needs_mac_download.csv",
                 "youtube_failures.csv", "youtube_retry.csv", "run_summary.json"):
        source = work / name
        if source.exists():
            try:
                subprocess.run([rclone, "copyto", str(source), f"{remote.rstrip('/')}/{name}"], check=True)
            except Exception as error:
                print(f"manifest_upload_error name={name} error={error}", flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Resumable all-source overnight Stoarama scanner")
    result.add_argument("--config", default="pipeline_config.json")
    result.add_argument("--work", type=Path, default=Path("work/overnight"))
    result.add_argument("--model", required=True)
    result.add_argument("--device", default="0")
    result.add_argument("--hours", type=float, default=5.75)
    result.add_argument("--refresh-catalog", action="store_true")
    result.add_argument("--drive-remote", default="pilotdrive:overnight_scan")
    result.add_argument("--max-sources", type=int, default=0)
    result.add_argument("--shard-count", type=int, default=1)
    result.add_argument("--shard-index", type=int, default=0)
    return result


def main() -> None:
    args = parser().parse_args()
    from ultralytics import YOLO
    import youtube_dvr_scan as youtube_engine

    config = load_config(args.config)
    args.work.mkdir(parents=True, exist_ok=True)
    clip_dir = args.work / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = args.work / "catalog_all.csv"
    if args.refresh_catalog or not catalog_path.exists():
        catalog = discover(config["stoarama_api"], config["source_types"])
        write_csv(catalog_path, catalog, CATALOG_FIELDS)
        print(f"catalog_records={len(catalog)}", flush=True)
    catalog = read_csv(catalog_path)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("shard-index must be between 0 and shard-count - 1")
    if args.shard_count > 1:
        catalog = [row for row in catalog if
                   int.from_bytes(hashlib.sha256(row["source_key"].encode()).digest()[:8], "big") % args.shard_count
                   == args.shard_index]
        print(f"shard={args.shard_index}/{args.shard_count} sources={len(catalog)}", flush=True)
    accepted_path, ledger_path = args.work / "selections_all.csv", args.work / "scan_ledger.csv"
    accepted = read_csv(accepted_path) if accepted_path.exists() else []
    ledger = read_csv(ledger_path) if ledger_path.exists() else []
    finished = {row["source_key"] for row in ledger}
    pending = round_robin([row for row in catalog if row["source_key"] not in finished])
    if args.max_sources:
        pending = pending[:args.max_sources]
    model = YOLO(args.model)
    youtube_engine.configure(config, args.device)
    deadline = time.monotonic() + args.hours * 3600
    examined = 0
    for row in pending:
        if time.monotonic() >= deadline:
            break
        examined += 1
        print(f"[{examined}] {row['capture_type']} {row.get('country') or 'Unknown'} | {row['name']}", flush=True)
        status, reason, result, local_path = "rejected", "no passing interval", None, None
        warning = known_unsuitable(row)
        try:
            if warning:
                reason = f"catalog warning: {warning}"
            elif row["capture_type"] == "youtube_watch":
                archived = None
                if int(float(row.get("captures_success") or 0)) > 0:
                    archived = rank_archive(row, model, config, args.device, clip_dir)
                if archived:
                    result, local_path = archived
                else:
                    candidates, dvr = youtube_engine.rank_video(
                        {**row, "review_url": row["youtube_url"]}, model,
                        int(config["lookback_hours"]), int(config["coarse_interval_minutes"]),
                        int(config["top_windows_per_video"]))
                    if candidates:
                        score, start, metrics = candidates[0]
                        duration = duration_for_score(float(score), config)
                        result = {**metrics, "score": float(score), "segment_start_utc": start.isoformat(),
                                  "segment_end_utc": (start + timedelta(seconds=duration)).isoformat(),
                                  "duration_seconds": duration, "provenance": "youtube_dvr", "stoarama_clip_id": ""}
                        try:
                            local_path = preserve_youtube(dvr, row, start, duration, clip_dir)
                        except Exception as error:
                            reason = f"selected; server preservation failed: {error}"
            else:
                archive = rank_archive(row, model, config, args.device, clip_dir)
                selected = archive or rank_live(row, model, config, args.device, clip_dir)
                if selected:
                    result, local_path = selected
            if result:
                drive_url = upload_status = link_status = ""
                if local_path:
                    try:
                        drive_url, upload_status, link_status = upload(local_path, args.drive_remote)
                    except Exception as error:
                        upload_status = f"ERROR: {error}"
                status = "accepted"
                accepted.append({
                    **{field: row.get(field, "") for field in ACCEPTED_FIELDS}, **result,
                    "row_id": len(accepted) + 1, "local_path": str(local_path or ""),
                    "drive_url": drive_url, "upload_status": upload_status,
                    "link_status": link_status, "status": "selected",
                })
                reason = reason if not local_path else ""
                print(f"  PASS score={float(result['score']):.3f} path={local_path or 'needs_mac'}", flush=True)
        except Exception as error:
            status, reason = "error", str(error)
            print(f"  ERROR {error}", flush=True)
        ledger.append({
            "source_key": row["source_key"], "stream_id": row["stream_id"], "name": row["name"],
            "capture_type": row["capture_type"], "country": row.get("country") or "",
            "status": status, "reason": reason, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        write_csv(accepted_path, accepted, ACCEPTED_FIELDS)
        write_csv(ledger_path, ledger, LEDGER_FIELDS)
        if examined % 10 == 0:
            finalize(accepted, args.work, config, args.drive_remote)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "normalized_catalog_sources": len(catalog), "examined_this_job": examined,
        "ledger_total": len(ledger), "accepted_total": len(accepted),
        "drive_ready_total": sum(row.get("link_status") == "verified" for row in accepted),
        "needs_mac_total": sum(row.get("capture_type") == "youtube_watch" and not row.get("drive_url") for row in accepted),
        "remaining_total": len(catalog) - len(ledger),
        "ledger_status_counts": {status: sum(row.get("status") == status for row in ledger)
                                 for status in sorted({row.get("status") for row in ledger})},
    }
    (args.work / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    finalize(accepted, args.work, config, args.drive_remote)
    print(f"finished_this_job={examined} accepted_total={len(accepted)} ledger_total={len(ledger)} pending={len(catalog)-len(ledger)}", flush=True)


if __name__ == "__main__":
    main()
