from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .common import read_csv


def validate_selection(path: str | Path) -> list[str]:
    errors = []
    rows = read_csv(path)
    required = {"row_id", "name", "video_id", "youtube_url", "segment_start_utc", "segment_end_utc", "duration_seconds", "score"}
    for index, row in enumerate(rows, 2):
        missing = sorted(key for key in required if not str(row.get(key, "")).strip())
        if missing:
            errors.append(f"line {index}: missing {', '.join(missing)}")
            continue
        try:
            start = datetime.fromisoformat(row["segment_start_utc"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(row["segment_end_utc"].replace("Z", "+00:00"))
            duration = int(float(row["duration_seconds"]))
            if abs((end - start).total_seconds() - duration) > 5:
                errors.append(f"line {index}: timestamp duration does not match duration_seconds")
            if duration not in {90, 120, 150}:
                errors.append(f"line {index}: duration must be 90, 120, or 150 seconds")
        except Exception as error:
            errors.append(f"line {index}: invalid timestamp or duration ({error})")
    return errors
