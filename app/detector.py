"""Decides which parts of a recording to keep. The scoring and selection
(`choose_segments`) are pure functions; ffmpeg/ffprobe live in media.py."""
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import NamedTuple

import numpy as np

from . import media
from .media import ProcessingError

MIN_SECONDS = 600

# Known language-code variants, keyed by the short code used in a profile's
# `languages` field (e.g. "nld,eng"). Unknown codes match as a literal tag.
LANG_PRESETS = {
    "nld": {"nld", "dut", "nl"},
    "eng": {"eng", "en"},
    "deu": {"deu", "ger", "de"},
    "fra": {"fra", "fre", "fr"},
    "spa": {"spa", "es"},
    "ita": {"ita", "it"},
}

# on_progress(stage, fraction) — fraction is 0..1, or None when unknown.
ProgressFn = Callable[[str, float | None], None]


@dataclass(frozen=True)
class CutConfig:
    """The cutting rules for one file; a plain value so it can cross threads."""
    target_fraction: float
    event_start_sec: int = 0
    end_trim_sec: int = 0
    intro_sec: int = 150
    outro_sec: int = 120
    clip_before: int = 20
    clip_after: int = 25
    min_separation: int = 60
    languages: str = "nld,eng"
    reencode: bool = False
    video_preset: str = media.DEFAULT_PRESET   # only used when re-encoding
    video_crf: int = media.DEFAULT_CRF
    video_encoder: str = media.ENCODER_SOFTWARE  # "software" (x264) or "hardware" (VAAPI GPU)

    @classmethod
    def from_profile(cls, profile) -> "CutConfig":
        return cls(**{f.name: getattr(profile, f.name) for f in fields(cls)})


class Result(NamedTuple):
    duration_sec: float
    audio_track: int
    encoder_note: str = ""      # only set when hardware was used, or wanted but not available


def language_groups(languages_csv: str) -> list[set[str]]:
    groups = []
    for code in (languages_csv or "").split(","):
        code = code.strip().lower()
        if code:
            groups.append(LANG_PRESETS.get(code, {code}))
    return groups


def pick_audio_track(stream_languages: list[str], groups: list[set[str]]) -> int:
    """Index of the first audio stream matching the most-preferred language
    group; falls back to the first stream."""
    for group in groups:
        for index, language in enumerate(stream_languages):
            if language in group:
                return index
    return 0


def _smooth(x, window: int):
    return np.convolve(x, np.ones(window) / window, mode="same")


def choose_segments(rms, total: float, target: float, cfg: CutConfig) -> list[tuple[float, float]]:
    """Intro + outro + the loudest 'moments' (speech peaks relative to the
    local average), until about `target` seconds are selected. Overlapping
    segments are merged. Returns sorted (start, end) pairs in seconds."""
    if len(rms) < MIN_SECONDS:
        raise ProcessingError("file too short (under 10 minutes)")
    start = cfg.event_start_sec
    end = min(total, len(rms)) - cfg.end_trim_sec
    if end - start < cfg.intro_sec + cfg.outro_sec:
        raise ProcessingError("the start/end trims and intro/outro leave no room for highlights")

    score = _smooth(rms, 5) / (_smooth(rms, 240) + 1e-6)
    segments = [(start, min(start + cfg.intro_sec, end)),
                (max(start, end - cfg.outro_sec), end)]
    used = cfg.intro_sec + cfg.outro_sec
    picked: list[int] = []
    for t in np.argsort(score)[::-1]:
        if t < start + cfg.intro_sec or t > end - cfg.outro_sec:
            continue
        if any(abs(t - p) < cfg.min_separation for p in picked):
            continue
        picked.append(int(t))
        used += cfg.clip_before + cfg.clip_after
        if used >= target:
            break
    segments += [(max(start, t - cfg.clip_before), min(end, t + cfg.clip_after)) for t in picked]

    segments.sort()
    merged = [list(segments[0])]
    for s, e in segments[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(float(s), float(e)) for s, e in merged]


def process(src: str, dest: str, cfg: CutConfig, on_progress: ProgressFn | None = None,
            cancel: threading.Event | None = None, runtime: media.Runtime | None = None) -> Result:
    """Cut `src` down per `cfg` and write the result to `dest`. `dest` only
    appears once a valid result exists (written to dest + '.part' first,
    then renamed). Raises ProcessingError on any failure, or media.Cancelled
    if `cancel` is set while it runs (ffmpeg is stopped and nothing is left behind)."""
    def report(stage: str, fraction: float | None = None):
        if on_progress:
            on_progress(stage, fraction)

    total = media.duration(src)
    target = (total - cfg.event_start_sec - cfg.end_trim_sec) * cfg.target_fraction
    if target <= 0:
        raise ProcessingError("the start/end trims leave nothing to cut")
    track = pick_audio_track(media.audio_languages(src), language_groups(cfg.languages))

    report("Analyzing audio", 0.0)
    rms = media.loudness(src, track, total, lambda f: report("Analyzing audio", f), cancel, runtime)
    segments = choose_segments(rms, total, target, cfg)

    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        report("Cutting highlights", 0.0)
        info = media.cut(src, tmp, segments, track, cfg.reencode,
                         lambda f: report("Cutting highlights", f), cancel,
                         cfg.video_preset, cfg.video_crf, cfg.video_encoder, runtime)
        length = media.duration(tmp)
        if not target * 0.5 <= length <= target * 1.5:
            raise ProcessingError(f"output duration {length:.0f}s looks wrong vs target "
                                  f"{target:.0f}s, keeping original")
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    note = ""
    if info.fallback:
        note = f"software encoder, because the hardware encoder is not available: {info.fallback}"
    elif info.how == media.ENCODER_HARDWARE:
        note = "hardware encoder (VAAPI)"
    return Result(length, track, note)
