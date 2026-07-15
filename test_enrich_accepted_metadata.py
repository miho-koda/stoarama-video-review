from enrich_accepted_metadata import add_stoarama, apply_oembed_metadata, browser_spec


def test_stoarama_enrichment_preserves_original_location_and_never_guesses():
    row = {"stream_id": "16", "city": "Tashkent", "region": "", "country": "Uzbekistan"}
    record = {"stream": {"provider": "youtube", "external_id": "x", "source_family": "youtube", "execution_class": "video_live",
                         "source_url": "https://example.test/watch", "source_page_url": "", "tags": ["a"],
                         "location_source": "import", "location_locality": "", "location_text": "Tashkent, Uzbekistan",
                         "recording_state": "on", "expected_fps": 30, "capture_runtime_status": "ok",
                         "metadata_json": {"import_source": "test", "list": "unit", "row_number": 1,
                                           "verified_at": "2026-01-01", "csv_values": {"valid": "yes", "why": ""}}},
              "item": {"captures_success": 2, "survey_last_person_count": 3, "survey_last_vehicle_count": 1,
                       "survey_last_sampled_at": "2026-01-01"}}
    add_stoarama(row, record, "2026-07-15T00:00:00+00:00")
    assert row["city"] == "Tashkent"
    assert row["stoarama_city_original"] == "Tashkent"
    assert row["verified_city"] == ""
    assert row["location_status"] == "unverified"
    assert row["stoarama_provider"] == "youtube"
    assert row["stoarama_tags"] == '["a"]'


def test_browser_spec_keeps_optional_profile_handling_outside_yt_dlp_options():
    assert browser_spec("chrome:Profile 1") == ("chrome", "Profile 1")


def test_oembed_is_explicitly_marked_partial():
    row = {"youtube_url": "https://www.youtube.com/watch?v=abc"}
    apply_oembed_metadata(row, {"title": "Camera", "author_name": "Operator", "author_url": "https://youtube.test/@operator",
                                "thumbnail_url": "https://image.test/thumb.jpg"}, "yt-dlp blocked")
    assert row["youtube_metadata_status"] == "partial_oembed"
    assert row["youtube_current_title"] == "Camera"
    assert "youtube_description" not in row
    assert row["youtube_error"] == "yt-dlp blocked"
