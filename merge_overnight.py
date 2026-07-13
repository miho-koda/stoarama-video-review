#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from overnight_scan import ACCEPTED_FIELDS, LEDGER_FIELDS, finalize
from stoarama_pipeline.common import load_config, read_csv, write_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge independent overnight scanner shards")
    parser.add_argument("shards", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/overnight/merged"))
    parser.add_argument("--config", default="pipeline_config.json")
    parser.add_argument("--drive-remote", default="pilotdrive:overnight_scan")
    args = parser.parse_args()
    accepted, ledger = [], []
    for shard in args.shards:
        selection = shard / "selections_all.csv"
        scan_ledger = shard / "scan_ledger.csv"
        if selection.exists():
            accepted.extend(read_csv(selection))
        if scan_ledger.exists():
            ledger.extend(read_csv(scan_ledger))
    unique_accepted = {}
    for row in sorted(accepted, key=lambda item: float(item.get("score") or 0), reverse=True):
        unique_accepted.setdefault(row["source_key"], row)
    accepted = list(unique_accepted.values())
    for index, row in enumerate(accepted, 1):
        row["row_id"] = index
    unique_ledger = {row["source_key"]: row for row in ledger}
    ledger = list(unique_ledger.values())
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "scan_ledger.csv", ledger, LEDGER_FIELDS)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "shards": [str(path) for path in args.shards], "ledger_total": len(ledger),
        "accepted_total": len(accepted),
        "drive_ready_total": sum(row.get("link_status") == "verified" for row in accepted),
        "needs_mac_total": sum(row.get("capture_type") == "youtube_watch" and not row.get("drive_url") for row in accepted),
        "ledger_status_counts": dict(Counter(row.get("status") for row in ledger)),
    }
    (args.output / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    finalize(accepted, args.output, load_config(args.config), args.drive_remote)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
