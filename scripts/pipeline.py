#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _paths import PROJECT_ROOT

from stoarama_pipeline.common import load_config, write_csv
from stoarama_pipeline.discover import CATALOG_FIELDS, discover
from stoarama_pipeline.validate import validate_selection


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Repeatable Stoarama social-mixing dataset pipeline")
    root.add_argument("--config", default=str(PROJECT_ROOT / "config" / "pipeline_config.json"))
    commands = root.add_subparsers(dest="command", required=True)
    find = commands.add_parser("discover", help="Fetch and normalize the current Stoarama YouTube catalog")
    find.add_argument("--output", default="work/catalog.csv"); find.add_argument("--max-records", type=int, default=0)
    choose = commands.add_parser("select", help="GPU scan and rank historical 90/120/150-second intervals")
    choose.add_argument("--catalog", default="work/catalog.csv"); choose.add_argument("--output", default="work/selections.csv")
    choose.add_argument("--rejected", default="work/rejected.csv"); choose.add_argument("--model", required=True)
    choose.add_argument("--target", type=int); choose.add_argument("--max-videos", type=int, default=0)
    choose.add_argument("--device", default="0"); choose.add_argument("--no-resume", action="store_true")
    complete = commands.add_parser("run", help="Discover the catalog and run resumable GPU selection")
    complete.add_argument("--catalog", default="work/catalog.csv"); complete.add_argument("--output", default="work/selections.csv")
    complete.add_argument("--rejected", default="work/rejected.csv"); complete.add_argument("--model", required=True)
    complete.add_argument("--target", type=int); complete.add_argument("--max-records", type=int, default=0)
    complete.add_argument("--max-videos", type=int, default=0); complete.add_argument("--device", default="0")
    complete.add_argument("--refresh-catalog", action="store_true"); complete.add_argument("--no-resume", action="store_true")
    check = commands.add_parser("validate", help="Validate selection schema, durations, and timestamps")
    check.add_argument("manifest")
    return root


def main() -> None:
    args = parser().parse_args(); config = load_config(args.config)
    if args.command == "discover":
        rows = discover(config["stoarama_api"], config["source_types"], args.max_records)
        write_csv(args.output, rows, CATALOG_FIELDS)
        print(f"discovered={len(rows)} output={args.output}")
    elif args.command == "select":
        from stoarama_pipeline.select import select
        accepted, rejected = select(args.catalog, args.output, args.rejected, config, args.model,
            args.target or int(config["target_clips"]), args.max_videos, args.device, not args.no_resume)
        print(f"selected={len(accepted)} rejected={len(rejected)} output={args.output}")
    elif args.command == "run":
        if args.refresh_catalog or not Path(args.catalog).exists():
            rows = discover(config["stoarama_api"], config["source_types"], args.max_records)
            write_csv(args.catalog, rows, CATALOG_FIELDS)
            print(f"discovered={len(rows)} output={args.catalog}")
        from stoarama_pipeline.select import select
        accepted, rejected = select(args.catalog, args.output, args.rejected, config, args.model,
            args.target or int(config["target_clips"]), args.max_videos, args.device, not args.no_resume)
        print(f"selected={len(accepted)} rejected={len(rejected)} output={args.output}")
    elif args.command == "validate":
        errors = validate_selection(args.manifest)
        if errors:
            for error in errors: print(f"ERROR {error}")
            raise SystemExit(1)
        print(f"valid={args.manifest}")


if __name__ == "__main__":
    main()
