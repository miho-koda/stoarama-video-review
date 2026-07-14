import csv
from pathlib import Path

import mac_vod_exchange as mac


def test_browser_spec_with_profile():
    assert mac.browser_spec("chrome:Profile 1") == ("chrome", "Profile 1")


def test_browser_spec_without_profile():
    assert mac.browser_spec("safari") == ("safari",)


def test_atomic_csv_round_trip(tmp_path: Path):
    path = tmp_path / "exchange.csv"
    rows = [{"video_id": "abc", "offset_seconds": 75.0}, {"video_id": "def", "offset_seconds": 150.0}]
    mac.write_csv(path, rows)
    assert mac.read_csv(path) == [{"video_id": "abc", "offset_seconds": "75.0"},
                                  {"video_id": "def", "offset_seconds": "150.0"}]
