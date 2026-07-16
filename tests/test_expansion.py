from datetime import datetime, timedelta, timezone
from pathlib import Path

from expand_accepted_sources import choose_non_overlapping, expansion_row, overlaps, shard_for
from stoarama_pipeline.common import load_config


BASE = datetime(2026, 7, 16, tzinfo=timezone.utc)


def candidate(score: float, seconds: int):
    return score, BASE + timedelta(seconds=seconds), {"people_min": 4, "fixed_camera_score": .9}


def test_touching_intervals_are_not_overlaps():
    assert not overlaps(BASE + timedelta(seconds=120), 120, BASE, BASE + timedelta(seconds=120))
    assert overlaps(BASE + timedelta(seconds=119), 120, BASE, BASE + timedelta(seconds=120))


def test_selection_excludes_parent_interval_and_caps_at_four():
    blocked = [(BASE, BASE + timedelta(seconds=120))]
    selected = choose_non_overlapping(
        [candidate(9, 0), candidate(8, 120), candidate(7, 240), candidate(6, 360), candidate(5, 480), candidate(4, 600)],
        blocked, 120, 4,
    )
    assert [item[1] for item in selected] == [BASE + timedelta(seconds=value) for value in (120, 240, 360, 480)]


def test_sharding_is_stable_and_within_range():
    assignments = [shard_for(f"youtube:{index:011d}", 6) for index in range(40)]
    assert assignments == [shard_for(f"youtube:{index:011d}", 6) for index in range(40)]
    assert all(0 <= value < 6 for value in assignments)


def test_expansion_row_preserves_parent_and_uses_fixed_duration():
    parent = {"row_id": "1", "source_key": "youtube:abc", "drive_url": "parent-link",
              "segment_start_utc": BASE.isoformat(), "segment_end_utc": (BASE + timedelta(seconds=150)).isoformat()}
    row = expansion_row(parent, 5.4, BASE + timedelta(seconds=600), {"people_min": 5}, 1, 120,
                        "expansion-120s-v1", "new-link", "uploaded", "verified", 99, True)
    assert row["parent_drive_url"] == "parent-link"
    assert row["duration_seconds"] == 120
    assert row["segment_end_utc"] == (BASE + timedelta(seconds=720)).isoformat()
    assert row["local_media_deleted"] == "true"


def test_expansion_policy_is_locked_to_strict_120_second_output():
    config = load_config()
    assert config["expansion"]["duration_seconds"] == 120
    assert config["expansion"]["clips_per_source"] == 4
    assert config["min_person_height_px"] == 60
    assert config["qualifying_frame_fraction"] == .8
    assert config["person_size_fraction"] == .7
    assert config["daylight_frame_fraction"] == .75
    assert config["fixed_camera_min_score"] == .65


def test_job_caps_concurrency_at_six():
    job = (Path(__file__).resolve().parents[1] / "jobs" / "expand_accepted_sources.sbatch").read_text()
    assert "#SBATCH --array=0-5%6" in job
