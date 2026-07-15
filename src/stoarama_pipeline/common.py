from __future__ import annotations

import csv
import json
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "pipeline_config.json"


def load_config(path: str | Path | None = None) -> dict:
    selected = Path(path) if path else DEFAULT_CONFIG
    with selected.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: str | Path, rows: list[dict], fields: list[str] | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not fields:
        raise ValueError("fields are required when writing an empty CSV")
    fields = fields or list(rows[0])
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(destination)


def youtube_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and "/" not in raw:
        return raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0]
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    if "/embed/" in parsed.path or "/live/" in parsed.path:
        return parsed.path.rstrip("/").split("/")[-1]
    return ""


def duration_for_score(score: float, config: dict) -> int:
    policy = config["duration_policy"]
    if score >= float(policy["excellent_min_score"]):
        return int(policy["excellent_seconds"])
    if score >= float(policy["good_min_score"]):
        return int(policy["good_seconds"])
    return int(policy["accepted_seconds"])
