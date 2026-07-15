from datetime import datetime, timedelta, timezone

from scan import Clip, contiguous_windows, youtube_playability, youtube_review_url


def clip(number: int, start: int, duration: int = 90) -> Clip:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Clip(number, base + timedelta(seconds=start), base + timedelta(seconds=start + duration), "url")


def test_contiguous_window_reaches_150_seconds():
    windows = list(contiguous_windows([clip(1, 0), clip(2, 90)], 150))
    assert [[item.id for item in window] for window in windows] == [[1, 2]]


def test_gap_breaks_window():
    assert list(contiguous_windows([clip(1, 0), clip(2, 100)], 150)) == []


def test_fixed_live_youtube_link_needs_no_timestamp():
    url = "https://www.youtube.com/watch?v=abc"
    assert youtube_review_url(url, None) == url


def test_vod_youtube_link_replaces_timestamp():
    url = "https://www.youtube.com/watch?v=abc&t=4s"
    assert youtube_review_url(url, 125.9) == "https://www.youtube.com/watch?v=abc&t=125s"


def test_youtube_playability_rejects_dead_live_recording():
    html = '"playabilityStatus":{"status":"UNPLAYABLE","reason":"This live stream recording is not available."}'
    assert youtube_playability(html) == ("UNPLAYABLE", "This live stream recording is not available.")
