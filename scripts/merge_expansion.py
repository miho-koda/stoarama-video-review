#!/usr/bin/env python3
"""Merge six expansion shards without touching the original 40-row dataset."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from _paths import PROJECT_ROOT
from expand_accepted_sources import EXPANSION_FIELDS, LEDGER_FIELDS
from stoarama_pipeline.common import load_config, read_csv, write_csv


def upload_report(path: Path, remote: str) -> None:
    rclone = os.path.expanduser("~/.local/bin/rclone")
    subprocess.run([rclone, "copyto", str(path), f"{remote.rstrip('/')}/{path.name}"], check=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("shards", nargs="+", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--drive-remote", required=True)
    result.add_argument("--config", default=str(PROJECT_ROOT / "config" / "pipeline_config.json"))
    return result


def main() -> None:
    args = parser().parse_args()
    config = load_config(args.config)
    cap = int(config["expansion"]["clips_per_source"])
    selections, ledger, fields = [], [], []
    for shard in args.shards:
        source = shard / "expansion_selections.csv"
        if source.exists():
            rows = read_csv(source)
            selections.extend(rows)
            fields.extend(rows[0].keys() if rows else [])
        source = shard / "expansion_ledger.csv"
        if source.exists():
            ledger.extend(read_csv(source))
    unique = {}
    for row in sorted(selections, key=lambda value: float(value.get("score") or 0), reverse=True):
        key = (row.get("parent_source_key"), row.get("segment_start_utc"))
        unique.setdefault(key, row)
    grouped = defaultdict(list)
    for row in unique.values():
        if row.get("link_status") == "verified":
            grouped[row["parent_source_key"]].append(row)
    merged = []
    for source_key in sorted(grouped):
        for rank, row in enumerate(sorted(grouped[source_key], key=lambda value: (-float(value.get("score") or 0), value["segment_start_utc"]))[:cap], 1):
            row["expansion_rank"] = rank
            row["row_id"] = len(merged) + 1
            merged.append(row)
    latest = {row["source_key"]: row for row in ledger}
    ledger = list(latest.values())
    for row in ledger:
        found = len(grouped.get(row["source_key"], []))
        row["uploaded_clips"] = found
        row["shortfall"] = max(0, cap - found)
        if row.get("status") == "complete" and found < cap and not row.get("reason"):
            row["reason"] = f"strict shortfall: {found}/{cap} verified clips"
    args.output.mkdir(parents=True, exist_ok=True)
    selection_fields = list(dict.fromkeys(["row_id"] + fields + EXPANSION_FIELDS))
    write_csv(args.output / "expansion_selections.csv", merged, selection_fields)
    write_csv(args.output / "expansion_ledger.csv", ledger, LEDGER_FIELDS)
    summary = {
        "run_revision": config["expansion"]["run_revision"], "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "shards": [str(shard) for shard in args.shards], "parent_sources": len(latest),
        "verified_clips": len(merged), "sources_with_four": sum(len(rows) >= cap for rows in grouped.values()),
        "shortfall_sources": sum(len(rows) < cap for rows in grouped.values()),
        "clips_by_source": {key: len(rows) for key, rows in sorted(grouped.items())},
        "ledger_status_counts": dict(Counter(row.get("status") for row in ledger)),
    }
    summary_path = args.output / "expansion_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for path in (args.output / "expansion_selections.csv", args.output / "expansion_ledger.csv", summary_path):
        upload_report(path, args.drive_remote)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
