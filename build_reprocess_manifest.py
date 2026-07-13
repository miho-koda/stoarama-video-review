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
    if row.get("status") == "unprocessed":
        return "unprocessed", "process_on_server"
    revision = row.get("scanner_revision") or legacy_revision
    if revision != SCANNER_REVISION:
        return "stale_scanner", "reprocess_on_server"
    if row.get("status") == "invalid_source":
        return "invalid_source", "do_not_retry_until_url_is_fixed"
    if row.get("status") != "error":
        return "complete", "do_not_reprocess"
    if row.get("capture_type") != "youtube_watch":
        return "server_error", "reprocess_on_server"
    failure, action = classify_youtube_failure(row.get("reason") or "")
    return failure, action


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an auditable source reprocessing manifest")
    parser.add_argument("ledgers", nargs="+", type=Path)
    parser.add_argument("--catalog", type=Path,
                        help="Canonical catalog; keys without a ledger row are marked unprocessed")
    parser.add_argument("--output", type=Path, default=Path("work/overnight/reprocess_manifest.csv"))
    parser.add_argument("--server-worklist-output", type=Path,
                        help="Write only unprocessed and retryable server-error sources here")
    parser.add_argument("--disposition-output", type=Path,
                        help="Write exactly one disposition row for every catalog source")
    parser.add_argument("--queue-dir", type=Path,
                        help="Write completed, Mac, VOD, invalid, and manual-review worklists")
    parser.add_argument(
        "--legacy-revision", default="",
        help="Known scanner revision for ledgers created before provenance columns existed")
    args = parser.parse_args()
    unique = {}
    for path in args.ledgers:
        for row in read_csv(path):
            unique[row["source_key"]] = row
    if args.catalog:
        for row in read_csv(args.catalog):
            unique.setdefault(row["source_key"], {
                **row, "status": "unprocessed", "reason": "no ledger result",
                "scanner_revision": SCANNER_REVISION,
            })
    output, dispositions = [], []
    for row in unique.values():
        category, action = classify(row, args.legacy_revision)
        disposition = {**row, "scanner_revision": row.get("scanner_revision") or args.legacy_revision,
                       "reprocess_class": category, "recommended_action": action}
        dispositions.append(disposition)
        if category == "complete":
            continue
        output.append(disposition)
    write_csv(args.output, output, FIELDS)
    server_worklist = [row for row in output if row["recommended_action"] in
                       {"process_on_server", "reprocess_on_server"}]
    if args.server_worklist_output:
        write_csv(args.server_worklist_output, server_worklist, FIELDS)
    if args.disposition_output:
        write_csv(args.disposition_output, dispositions, FIELDS)
    if args.queue_dir:
        args.queue_dir.mkdir(parents=True, exist_ok=True)
        queues = {
            "completed.csv": [row for row in dispositions if row["reprocess_class"] == "complete"],
            "mac_cookie_worklist.csv": [row for row in dispositions if row["reprocess_class"] == "bot_or_auth"],
            "vod_worklist.csv": [row for row in dispositions if row["reprocess_class"] == "vod_or_non_dvr"],
            "invalid_sources.csv": [row for row in dispositions if row["reprocess_class"] in
                                    {"unavailable", "invalid_source"}],
            "manual_review_worklist.csv": [row for row in dispositions if row["reprocess_class"] ==
                                           "other_extraction_error"],
        }
        for name, rows in queues.items():
            write_csv(args.queue_dir / name, rows, FIELDS)
    print(f"ledger_sources={len(unique)} reprocess_sources={len(output)} output={args.output}")
    if args.server_worklist_output:
        print(f"server_worklist_sources={len(server_worklist)} output={args.server_worklist_output}")
    print(f"disposition_sources={len(dispositions)}")


if __name__ == "__main__":
    main()
