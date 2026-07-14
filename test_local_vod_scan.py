import json
from pathlib import Path
from unittest.mock import patch

import pytest

import local_vod_scan as scan


def test_selects_smallest_usable_proxy():
    formats = [
        {"format_id": "240", "height": 240, "vcodec": "avc", "filesize": 900},
        {"format_id": "144", "height": 144, "vcodec": "avc", "filesize": 400},
        {"format_id": "audio", "height": 0, "vcodec": "none", "filesize": 1},
    ]
    assert scan.select_proxy_format(formats, 100)["format_id"] == "144"


def test_refuses_proxy_over_2gb_and_unknown_size():
    with pytest.raises(ValueError, match="proxy_over_2gb"):
        scan.select_proxy_format([{"height": 144, "vcodec": "avc", "filesize": scan.PROXY_CAP + 1}], 100)
    with pytest.raises(ValueError, match="proxy_size_unknown"):
        scan.select_proxy_format([{"height": 144, "vcodec": "avc"}], 100)


def test_overlapping_windows_cover_tail():
    windows = scan.overlapping_windows(401)
    assert windows == [(0.0, 150.0), (120.0, 150.0), (240.0, 150.0), (251.0, 150.0)]
    assert scan.overlapping_windows(149) == []


def test_coarse_rejection_reason_explains_the_proxy_screen():
    detail = scan.coarse_diagnostic_reason({"windows": 8, "decoded": 8, "daylight": 1, "density": 2,
                                            "eligible": 0, "best_daylight": .625, "people_min": 0,
                                            "people_max": 42})
    assert detail == ("no_promising_windows:windows=8;decoded=8;daylight=1;density=2;eligible=0;"
                      "best_daylight=0.625;people_median_range=0.0-42.0")


def test_duration_policy_and_top_two_non_overlap():
    config = {"duration_policy": {"excellent_min_score": 5.5, "excellent_seconds": 150,
                                  "good_min_score": 4, "good_seconds": 120, "accepted_seconds": 90}}
    assert [scan.duration_for_score(score, config) for score in (3, 4, 5.5)] == [90, 120, 150]
    rows = [{"start": 0, "duration": 150, "score": 9}, {"start": 30, "duration": 90, "score": 8},
            {"start": 151, "duration": 90, "score": 7}]
    assert [row["start"] for row in scan.choose_non_overlapping(rows, minimum_gap=0)] == [0, 151]


def test_top_two_requires_meaningful_separation():
    rows = [{"start": 0, "duration": 120, "score": 9}, {"start": 120, "duration": 120, "score": 8},
            {"start": 421, "duration": 120, "score": 7}]
    assert [row["start"] for row in scan.choose_non_overlapping(rows)] == [0, 421]


def test_timestamp_derivation_is_conservative():
    assert scan.derive_timestamps({}, 10, 90)["segment_start_utc"] == ""
    result = scan.derive_timestamps({"release_timestamp": 0}, 10, 90)
    # Zero is a valid epoch value even though YouTube will not normally provide it.
    assert result["recording_time_confidence"] == "unavailable"
    result = scan.derive_timestamps({"release_timestamp": 1000}, 10, 90)
    assert result["segment_start_utc"] == "1970-01-01T00:16:50+00:00"


@pytest.mark.parametrize("field,value,expected", [
    ("fixed_camera_score", .2, "ptz_or_moving_camera"),
    ("camera_assessment", "obvious_high_view", "obvious_high_camera"),
    ("daylight_fraction", .2, "night_or_low_light"),
    ("people_max", 31, "excessive_crowd"),
    ("people_ge60_fraction", .2, "undersized_people"),
])
def test_rejection_classification(field, value, expected):
    metrics = {"fixed_camera_score": 1, "camera_assessment": "not_obviously_high", "daylight_fraction": 1,
               "people_max": 10, "people_ge60_fraction": 1, "dense_stability_score": 1, "vehicles_total": 0, "people_total": 10}
    metrics[field] = value
    assert scan.classify_rejection(metrics) == expected


def test_dense_camera_motion_rejects_an_otherwise_good_clip():
    metrics = {"fixed_camera_score": 1, "dense_stability_score": .2, "camera_assessment": "not_obviously_high",
               "daylight_fraction": 1, "people_max": 10, "people_ge60_fraction": 1, "vehicles_total": 0, "people_total": 10}
    assert scan.classify_rejection(metrics) == "ptz_or_moving_camera"


def test_dense_stability_rejects_pan_rotation_and_zoom():
    identity = __import__("numpy").array([[1., 0., 0.], [0., 1., 0.]])
    pan = __import__("numpy").array([[1., 0., 2.], [0., 1., 0.]])
    rotation = __import__("numpy").array([[.9999, -.01, 0.], [.01, .9999, 0.]])
    zoom = __import__("numpy").array([[1.01, 0., 0.], [0., 1.01, 0.]])
    assert scan.stable_camera_pair(scan.affine_motion(identity))
    assert not scan.stable_camera_pair(scan.affine_motion(pan))
    assert not scan.stable_camera_pair(scan.affine_motion(rotation))
    assert not scan.stable_camera_pair(scan.affine_motion(zoom))


def test_person_size_metrics_keep_60px_acceptance_and_report_distribution(monkeypatch):
    frame = __import__("numpy").zeros((100, 100, 3), dtype="uint8")
    stats = [{"people": 5, "vehicles": 0, "pairs": 1, "daylight": 1, "all_heights": [70, 85, 90, 95, 100]}] * 12
    monkeypatch.setattr(scan, "frame_metrics", lambda *args: stats[0])
    monkeypatch.setattr(scan, "fixed_camera_score", lambda frames: 1)
    metrics = scan.analyse_frames([frame] * 12, object(), {}, "cpu", full=True)
    assert metrics["people_ge60_fraction"] == 1
    assert metrics["people_ge80_fraction"] == .8
    assert metrics["size_frame_pass_fraction"] == 1
    assert metrics["passed"]


def test_atomic_resume_skips_completed_source(tmp_path: Path):
    args = type("Args", (), {"work": tmp_path, "config": Path("pipeline_config.json"), "model": Path("model.pt")})
    runner = scan.Scanner(args)
    row = {"source_key": "youtube:a", "video_id": "a", "name": "A", "status": "complete", "attempts": 1}
    scan.atomic_csv(runner.paths.ledger, [row], scan.LEDGER_FIELDS)
    assert scan.read_csv(runner.paths.ledger)[0]["status"] == "complete"
    assert not runner.paths.ledger.with_suffix(".csv.tmp").exists()


def test_cleanup_keeps_only_current_part_on_interrupt(tmp_path: Path):
    args = type("Args", (), {"work": tmp_path, "config": Path("pipeline_config.json"), "model": Path("model.pt")})
    runner = scan.Scanner(args); runner.paths.current.mkdir()
    (runner.paths.current / "proxy.part").write_bytes(b"resume")
    (runner.paths.current / "candidate.mp4").write_bytes(b"video")
    (runner.paths.current / "frame.jpg").write_bytes(b"forbidden")
    runner.cleanup(preserve_parts=True)
    assert [p.name for p in runner.paths.current.iterdir()] == ["proxy.part"]


def test_upload_is_verified_before_local_delete(tmp_path: Path):
    local = tmp_path / "clip.mp4"; local.write_bytes(b"1234")
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        class Result: pass
        result = Result()
        result.stdout = json.dumps([{"Size": 4}]) if command[1] == "lsjson" else "https://drive.test/clip\n"
        return result
    with patch.object(scan, "run", fake_run):
        assert scan.verify_upload(local, "remote:clip.mp4").startswith("https://")
    assert local.exists()
    assert [call[1] for call in calls] == ["lsjson", "link"]


def test_failed_upload_verification_retains_local_file(tmp_path: Path):
    local = tmp_path / "clip.mp4"; local.write_bytes(b"1234")
    class Result: stdout = '[{"Size": 3}]'
    with patch.object(scan, "run", return_value=Result()):
        with pytest.raises(scan.UploadVerificationError, match="remote_size"):
            scan.verify_upload(local, "remote:clip.mp4")
    assert local.exists()


def test_source_contains_no_jpg_creation_path():
    source = Path(scan.__file__).read_text()
    assert 'imwrite' not in source
    assert '".jpg"' not in source
