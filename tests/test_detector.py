import numpy as np
import pytest

from app import detector
from app.detector import CutConfig, choose_segments
from app.media import ProcessingError


def test_language_groups_and_track_choice():
    groups = detector.language_groups("nld, ENG,xx,")
    assert groups[0] >= {"nld", "dut", "nl"}
    assert groups[2] == {"xx"}          # unknown code = literal tag
    assert detector.pick_audio_track(["eng", "dut"], groups) == 1   # nld preferred over eng
    assert detector.pick_audio_track(["fra", "deu"], groups) == 0   # no match: first track
    assert detector.pick_audio_track([], groups) == 0


def _rms(seconds=3600, spikes=(1000, 2000, 3000)):
    rms = np.full(seconds, 100.0)
    for t in spikes:
        rms[t:t + 8] = 3000.0
    return rms


def test_choose_segments_keeps_intro_outro_and_loud_moments():
    cfg = CutConfig(target_fraction=0.1, intro_sec=100, outro_sec=100, clip_before=10, clip_after=10)
    segments = choose_segments(_rms(), 3600, 360, cfg)
    assert segments[0][0] == 0 and segments[0][1] >= 100
    assert segments[-1][1] == 3600
    assert any(s <= 1000 <= e for s, e in segments)          # a spike was picked
    assert all(a[1] < b[0] for a, b in zip(segments, segments[1:], strict=False))   # sorted, merged
    kept = sum(e - s for s, e in segments)
    assert 200 <= kept <= 600


def test_choose_segments_respects_trims():
    cfg = CutConfig(target_fraction=0.2, event_start_sec=300, end_trim_sec=200,
                    intro_sec=60, outro_sec=60)
    segments = choose_segments(_rms(), 3600, 600, cfg)
    assert segments[0][0] == 300
    assert segments[-1][1] == 3400


def test_choose_segments_rejects_short_files_and_impossible_trims():
    cfg = CutConfig(target_fraction=0.25)
    with pytest.raises(ProcessingError, match="too short"):
        choose_segments(np.ones(300), 300, 75, cfg)
    with pytest.raises(ProcessingError, match="no room"):
        choose_segments(_rms(), 3600, 900, CutConfig(target_fraction=0.25, event_start_sec=3500))


def test_cutconfig_from_profile_ignores_extra_fields():
    from app.models import Profile
    p = Profile(name="x", target_fraction=0.3, delete_original=True)
    cfg = CutConfig.from_profile(p)
    assert cfg.target_fraction == 0.3 and not hasattr(cfg, "delete_original")
