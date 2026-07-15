#!/usr/bin/env python3
"""Freeze the server-only catalog after excluding local and accepted sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stoarama_pipeline.common import read_csv, write_csv
from stoarama_pipeline.discover import CATALOG_FIELDS


def build_remainder(catalog: list[dict], exclusions: list[list[dict]]) -> tuple[list[dict], dict]:
    excluded = {row["source_key"] for rows in exclusions for row in rows if row.get("source_key")}
    seen, remainder = set(), []
    for row in catalog:
        key = row.get("source_key")
        if not key or key in excluded or key in seen:
            continue
        seen.add(key); remainder.append(row)
    summary = {"catalog_sources": len({row.get("source_key") for row in catalog if row.get("source_key")}),
               "excluded_sources": len(excluded), "server_sources": len(remainder),
               "capture_type_counts": {kind: sum(row.get("capture_type") == kind for row in remainder)
                                       for kind in sorted({row.get("capture_type") for row in remainder})}}
    return remainder, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--exclude", action="append", type=Path, required=True,
                        help="CSV containing source_key values; repeat for each exclusion set")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    catalog = read_csv(args.catalog)
    exclusions = [read_csv(path) for path in args.exclude]
    remainder, summary = build_remainder(catalog, exclusions)
    write_csv(args.output, remainder, CATALOG_FIELDS)
    summary_path = args.summary or args.output.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
