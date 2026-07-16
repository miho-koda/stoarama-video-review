from merge_legacy_pilot import merge


def current(start="2026-07-10T10:00:00Z", end="2026-07-10T10:02:00Z"):
    return {"row_id": "1", "youtube_url": "https://www.youtube.com/watch?v=abc", "segment_start_utc": start,
            "segment_end_utc": end, "source_key": "youtube:abc"}


def pilot(start="2026-07-10T10:02:00Z", end="2026-07-10T10:04:00Z"):
    return {"row_id": "1", "filename": "pilot.mp4", "name": "Pilot", "street_or_area": "Street", "city": "City",
            "region": "Region", "country": "Country", "recording_start_utc": start, "recording_end_utc": end,
            "recording_start_local": "", "recording_end_local": "", "duration_seconds": "120", "annotation_score": "5",
            "people_min": "4", "people_median": "6", "people_max": "8", "source_youtube_url": "https://www.youtube.com/watch?v=abc",
            "drive_url": "https://drive.google.com/open?id=x", "timestamp_accuracy": "approx", "timestamp_source": "DVR"}


def test_pilot_rows_are_added_with_explicit_legacy_provenance():
    result = merge([current()], [pilot()])
    assert len(result) == 2
    assert result[1]["selection_origin"] == "pilot_legacy"
    assert result[1]["quality_gate_status"] == "not_revalidated"
    assert result[1]["row_id"] == "2"


def test_overlapping_pilot_interval_is_rejected():
    overlapping = pilot("2026-07-10T10:01:00Z", "2026-07-10T10:03:00Z")
    try:
        merge([current()], [overlapping])
    except ValueError as error:
        assert "overlaps" in str(error)
    else:
        raise AssertionError("expected overlap rejection")
