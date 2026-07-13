#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from overnight_scan import ACCEPTED_FIELDS, LEDGER_FIELDS, finalize
from stoarama_pipeline.common import load_config, read_csv, write_csv
from stoarama_pipeline.discover import CATALOG_FIELDS
from stoarama_pipeline.link_audit import classify_link_failure, source_review_url


FAILURE_FIELDS = list(dict.fromkeys(CATALOG_FIELDS + ["scan_error", "failure_class", "recommended_action"]))
LINK_FAILURE_FIELDS = list(dict.fromkeys(CATALOG_FIELDS + LEDGER_FIELDS))


def classify_youtube_failure(reason: str) -> tuple[str, str]:
    lowered = reason.lower()
    if "sign in to confirm" in lowered or "cookies" in lowered or "not a bot" in lowered:
        return "bot_or_auth", "retry_on_mac_with_browser_cookies"
    if "list index out of range" in lowered:
        return "vod_or_non_dvr", "run_vod_timestamp_scanner"
    if any(text in lowered for text in ("video unavailable", "recording is not available", "private video")):
        return "unavailable", "do_not_retry_unless_source_status_changes"
    return "other_extraction_error", "retry_on_mac_then_manual_review"


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge independent overnight scanner shards")
    parser.add_argument("shards", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/overnight/merged"))
    parser.add_argument("--config", default="pipeline_config.json")
    parser.add_argument("--drive-remote", default="pilotdrive:overnight_scan")
    args = parser.parse_args()
    accepted, ledger, catalogs = [], [], []
    for shard in args.shards:
        selection = shard / "selections_all.csv"
        scan_ledger = shard / "scan_ledger.csv"
        if selection.exists():
            accepted.extend(read_csv(selection))
        if scan_ledger.exists():
            ledger.extend(read_csv(scan_ledger))
        catalog = shard / "catalog_all.csv"
        if catalog.exists():
            catalogs.extend(read_csv(catalog))
    unique_accepted = {}
    for row in sorted(accepted, key=lambda item: float(item.get("score") or 0), reverse=True):
        unique_accepted.setdefault(row["source_key"], row)
    accepted = list(unique_accepted.values())
    for index, row in enumerate(accepted, 1):
        row["row_id"] = index
    unique_ledger = {row["source_key"]: row for row in ledger}
    ledger = list(unique_ledger.values())
    catalog_by_key = {row["source_key"]: row for row in catalogs}
    # Backfill link audit fields for ledgers written by workers that were
    # already running when link auditing was introduced.
    for row in ledger:
        if row.get("status") == "error" and not row.get("link_failure_class"):
            failure_class, action = classify_link_failure(row.get("reason") or "")
            row["link_failure_class"] = failure_class
            row["recommended_action"] = action
            row["source_link_status"] = failure_class
            row["resolved_source_url"] = source_review_url(catalog_by_key.get(row["source_key"], row))
            if failure_class in {"permanently_unavailable", "restricted"}:
                row["status"] = "invalid_source"
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "scan_ledger.csv", ledger, LEDGER_FIELDS)
    youtube_failures = []
    for row in ledger:
        if row.get("capture_type") != "youtube_watch" or row.get("status") not in {"error", "invalid_source"}:
            continue
        failure_class, action = classify_youtube_failure(row.get("reason") or "")
        youtube_failures.append({**catalog_by_key.get(row["source_key"], {}),
                                 "scan_error": row.get("reason") or "",
                                 "failure_class": failure_class, "recommended_action": action})
    youtube_retry = [row for row in youtube_failures if row["failure_class"] != "unavailable"]
    write_csv(args.output / "youtube_failures.csv", youtube_failures, FAILURE_FIELDS)
    write_csv(args.output / "youtube_retry.csv", youtube_retry, FAILURE_FIELDS)
    invalid_links = [{**catalog_by_key.get(row["source_key"], {}), **row} for row in ledger
                     if row.get("status") == "invalid_source"]
    temporary_link_failures = [{**catalog_by_key.get(row["source_key"], {}), **row} for row in ledger
                               if row.get("link_failure_class") in {"temporary_failure", "extraction_failure"}]
    write_csv(args.output / "invalid_links.csv", invalid_links, LINK_FAILURE_FIELDS)
    write_csv(args.output / "temporary_link_failures.csv", temporary_link_failures, LINK_FAILURE_FIELDS)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "shards": [str(path) for path in args.shards], "ledger_total": len(ledger),
        "accepted_total": len(accepted),
        "drive_ready_total": sum(row.get("link_status") == "verified" for row in accepted),
        "needs_mac_total": sum(row.get("capture_type") == "youtube_watch" and not row.get("drive_url") for row in accepted),
        "youtube_failure_total": len(youtube_failures), "youtube_retry_total": len(youtube_retry),
        "youtube_failure_class_counts": dict(Counter(row["failure_class"] for row in youtube_failures)),
        "invalid_link_total": len(invalid_links),
        "temporary_link_failure_total": len(temporary_link_failures),
        "ledger_status_counts": dict(Counter(row.get("status") for row in ledger)),
    }
    (args.output / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    finalize(accepted, args.output, load_config(args.config), args.drive_remote)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
