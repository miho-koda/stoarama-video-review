import csv
import tempfile
import types
import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from stoarama_pipeline.common import duration_for_score, youtube_id
from stoarama_pipeline.discover import discover
from stoarama_pipeline.validate import validate_selection


class PipelineTests(unittest.TestCase):
    def test_youtube_id_variants(self):
        self.assertEqual(youtube_id("https://www.youtube.com/watch?v=_0wPODlF9wU"), "_0wPODlF9wU")
        self.assertEqual(youtube_id("https://youtu.be/_0wPODlF9wU"), "_0wPODlF9wU")
        self.assertEqual(youtube_id("https://www.youtube.com/live/_0wPODlF9wU"), "_0wPODlF9wU")

    def test_duration_policy(self):
        config = {"duration_policy": {"excellent_min_score": 5.5, "excellent_seconds": 150,
                  "good_min_score": 4, "good_seconds": 120, "accepted_seconds": 90}}
        self.assertEqual(duration_for_score(5.5, config), 150)
        self.assertEqual(duration_for_score(4.2, config), 120)
        self.assertEqual(duration_for_score(3.9, config), 90)

    def test_selection_validator(self):
        fields = ["row_id", "name", "video_id", "youtube_url", "segment_start_utc",
                  "segment_end_utc", "duration_seconds", "score"]
        row = {"row_id": 1, "name": "test", "video_id": "_0wPODlF9wU",
               "youtube_url": "https://www.youtube.com/watch?v=_0wPODlF9wU",
               "segment_start_utc": "2026-07-12T12:00:00+00:00",
               "segment_end_utc": "2026-07-12T12:02:00+00:00", "duration_seconds": 120, "score": 5}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerow(row)
            self.assertEqual(validate_selection(path), [])
            row["duration_seconds"] = 100
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerow(row)
            self.assertTrue(validate_selection(path))

    @patch("stoarama_pipeline.discover.fetch_json")
    def test_discovery_normalizes_and_deduplicates(self, fetch):
        def item(stream_id, video_id):
            return {"stream": {"id": stream_id, "name": f"camera {stream_id}",
                "source_page_url": f"https://www.youtube.com/watch?v={video_id}",
                "location_city": "Test City", "location_country": "Test Country"}}
        fetch.return_value = {"items": [item(1, "_0wPODlF9wU"), item(2, "_0wPODlF9wU")], "total": 2}
        rows = discover("https://example.test/api", ["youtube_watch"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stream_id"], 1)
        self.assertEqual(rows[0]["city"], "Test City")

    def test_selector_writes_resumable_manifest(self):
        from stoarama_pipeline.select import select
        fake_ultralytics = types.ModuleType("ultralytics")
        fake_ultralytics.YOLO = lambda path: object()
        fake_engine = types.ModuleType("youtube_dvr_scan")
        fake_engine.configure = lambda config, device: None
        metrics = {"passed": True, "score": 5.6, "people_min": 4, "people_median": 8,
            "people_max": 12, "people_ge60_fraction": .9, "daylight_fraction": 1,
            "fixed_camera_score": .98, "social_pair_score": .7,
            "active_density_fraction": .9, "vehicles_total": 0}
        fake_engine.rank_video = lambda *args: ([(5.6, datetime(2026, 7, 12, 12, tzinfo=timezone.utc), metrics)], None)
        config = {"lookback_hours": 119, "coarse_interval_minutes": 30, "top_windows_per_video": 8,
            "duration_policy": {"excellent_min_score": 5.5, "excellent_seconds": 150,
                "good_min_score": 4, "good_seconds": 120, "accepted_seconds": 90}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {
                "ultralytics": fake_ultralytics, "youtube_dvr_scan": fake_engine}):
            catalog = Path(directory) / "catalog.csv"; selected = Path(directory) / "selected.csv"; rejected = Path(directory) / "rejected.csv"
            with catalog.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["stream_id", "name", "video_id", "youtube_url", "city", "region", "country"])
                writer.writeheader(); writer.writerow({"stream_id": 1, "name": "test", "video_id": "_0wPODlF9wU",
                    "youtube_url": "https://www.youtube.com/watch?v=_0wPODlF9wU", "city": "Canmore", "region": "Alberta", "country": "Canada"})
            accepted, failures = select(catalog, selected, rejected, config, "model.pt", 1, 1, "0")
            self.assertEqual(len(accepted), 1); self.assertEqual(failures, [])
            self.assertEqual(accepted[0]["duration_seconds"], 150)
            self.assertEqual(validate_selection(selected), [])


if __name__ == "__main__":
    unittest.main()
