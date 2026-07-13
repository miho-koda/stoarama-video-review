#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from merge_overnight import classify_youtube_failure
from overnight_scan import SCANNER_REVISION
from stoarama_pipeline.common import read_csv, write_csv


FIELDS = [
    "source_key", "stream_id", "name", "capture_type", "country", "status", "reason",
    "scanner_revision", "config_fingerprint", "finished_at_utc", "reprocess_class", "recommended_action",
]


def classify(row: dict, legacy_revision: str) -> tuple[str, str]:
    revision = row.get("scanner_revision") or legacy_revision
    if revision != SCANNER_REVISION:
        return "stale_scanner", "reprocess_on_server"
    if row.get("status") != "error":
        return "complete", "do_not_reprocess"
    if row.get("capture_type") != "youtube_watch":
        return "server_error", "reprocess_on_server"
    failure, action = classify_youtube_failure(row.get("reason") or "")
    return failure, action


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an auditable source reprocessing manifest")
    parser.add_argument("ledgers", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/overnight/reprocess_manifest.csv"))
    parser.add_argument(
        "--legacy-revision", default="",
        help="Known scanner revision for ledgers created before provenance columns existed")
    args = parser.parse_args()
    unique = {}
    for path in args.ledgers:
        for row in read_csv(path):
            unique[row["source_key"]] = row
    output = []
    for row in unique.values():
        category, action = classify(row, args.legacy_revision)
        if category == "complete":
            continue
        output.append({**row, "scanner_revision": row.get("scanner_revision") or args.legacy_revision,
                       "reprocess_class": category, "recommended_action": action})
    write_csv(args.output, output, FIELDS)
    print(f"ledger_sources={len(unique)} reprocess_sources={len(output)} output={args.output}")


if __name__ == "__main__":
    main()
