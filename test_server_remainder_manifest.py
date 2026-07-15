from build_server_remainder_manifest import build_remainder


def test_build_remainder_excludes_local_and_accepted_without_duplicates():
    catalog = [
        {"source_key": "youtube:a", "capture_type": "youtube_watch"},
        {"source_key": "hls:b", "capture_type": "hls"},
        {"source_key": "youtube:a", "capture_type": "youtube_watch"},
        {"source_key": "http:c", "capture_type": "http_video"},
    ]
    remainder, summary = build_remainder(catalog, [[{"source_key": "youtube:a"}], [{"source_key": "http:c"}]])
    assert [row["source_key"] for row in remainder] == ["hls:b"]
    assert summary == {"catalog_sources": 3, "excluded_sources": 2, "server_sources": 1,
                       "capture_type_counts": {"hls": 1}}
