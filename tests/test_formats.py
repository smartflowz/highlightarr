"""Recordings in every supported container are cut correctly, with real ffmpeg.

The samples are 5 minutes long; the 10-minute minimum is lowered to match.
"""
import dataclasses
import json
import logging
import os
import shutil
import subprocess

import pytest

from app import detector, media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

LENGTH = 300
CFG = detector.CutConfig(target_fraction=0.3, intro_sec=10, outro_sec=10, clip_before=5, clip_after=5,
                         min_separation=20, languages="nld,eng")
VIDEO = f"testsrc=size=160x90:rate=5:duration={LENGTH}"
AUDIO = (f"sine=f=500:duration={LENGTH}:sample_rate=48000,"
         "volume='if(lt(mod(t,60),5),1,0.08)':eval=frame")
H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25"]

# name -> (file name, extra ffmpeg arguments)
FORMATS = {
    "mp4": ("clip.mp4", [*H264, "-c:a", "aac"]),
    "mov": ("clip.mov", [*H264, "-c:a", "aac"]),
    "avi": ("old.avi", ["-c:v", "mpeg4", "-q:v", "5", "-g", "25", "-c:a", "libmp3lame"]),
    "ts": ("tv.ts", [*H264, "-c:a", "aac"]),
    "ts-late-start": ("late.ts", [*H264, "-c:a", "aac", "-output_ts_offset", "7200"]),
    "m2ts": ("disc.m2ts", [*H264, "-c:a", "ac3", "-f", "mpegts"]),
    "wmv": ("old.wmv", ["-c:v", "wmv2", "-q:v", "5", "-g", "25", "-c:a", "wmav2"]),
    "flv": ("flash.flv", ["-c:v", "flv", "-q:v", "5", "-g", "25", "-c:a", "libmp3lame", "-ar", "44100"]),
    "mpg": ("dvd.mpg", ["-c:v", "mpeg2video", "-q:v", "5", "-g", "12", "-c:a", "mp2"]),
    "3gp": ("phone.3gp", [*H264, "-c:a", "aac"]),
    "webm": ("web.webm", ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-g", "25",
                          "-c:a", "libopus"]),
}


@pytest.fixture(autouse=True)
def short_files(monkeypatch):
    monkeypatch.setattr(detector, "MIN_SECONDS", 250)   # the detector compares with a 240 s window


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    folder = tmp_path_factory.mktemp("formats")
    out = {}
    for key, (name, args) in FORMATS.items():
        path = str(folder / name)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", VIDEO,
                        "-f", "lavfi", "-i", AUDIO, *args, "-shortest", path], check=True)
        out[key] = path
    # An MP4 with MP4-style text subtitles, which Matroska cannot take as they are.
    srt = str(folder / "subs.srt")
    with open(srt, "w") as f:
        f.write("1\n00:00:05,000 --> 00:00:09,000\nHello\n\n2\n00:01:30,000 --> 00:01:34,000\nWorld\n")
    path = str(folder / "subs.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", VIDEO, "-f", "lavfi", "-i", AUDIO,
                    "-i", srt, "-map", "0:v", "-map", "1:a", "-map", "2:s", *H264, "-c:a", "aac",
                    "-c:s", "mov_text", "-t", str(LENGTH), path], check=True)
    out["mp4-subs"] = path
    return out


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return [(s["codec_type"], s["codec_name"]) for s in json.loads(out)["streams"]]


def _loud_share(path):
    rms = media.loudness(path, 0, media.duration(path))
    return float((rms > 0.5 * rms.max()).mean())


def _check_highlights(source, dest):
    types = [kind for kind, _ in _streams(dest)]
    assert types.count("video") == 1 and types.count("audio") == 1, _streams(dest)
    assert os.path.exists(dest) and not os.path.exists(dest + ".part")
    # the loud moments were what got kept, so they make up a bigger share of the result
    assert _loud_share(dest) > 1.3 * _loud_share(source)


@pytest.mark.parametrize("key", list(FORMATS))
def test_stream_copy_works_for_every_container(samples, tmp_path, key):
    dest = str(tmp_path / f"{key} - Highlights.mkv")
    detector.process(samples[key], dest, CFG)
    _check_highlights(samples[key], dest)


@pytest.mark.parametrize("key", ["ts-late-start", "avi", "mp4", "mpg", "flv"])
def test_reencoding_works_for_other_containers(samples, tmp_path, key):
    dest = str(tmp_path / f"{key} - Highlights.mkv")
    cfg = dataclasses.replace(CFG, reencode=True, video_preset="ultrafast")
    detector.process(samples[key], dest, cfg)
    _check_highlights(samples[key], dest)
    assert ("video", "h264") in _streams(dest) and ("audio", "aac") in _streams(dest)


def test_recordings_that_start_late_on_their_own_timeline(samples):
    assert media.start_time(samples["ts-late-start"]) > 7000
    assert media.start_time(samples["mp4"]) < 1


def test_mp4_text_subtitles_are_converted_for_matroska(samples, tmp_path):
    dest = str(tmp_path / "subs - Highlights.mkv")
    detector.process(samples["mp4-subs"], dest, CFG)
    assert ("subtitle", "subrip") in _streams(dest)
    assert media.subtitle_plan(samples["mp4-subs"]) == [(2, "srt")]


def test_subtitles_ffmpeg_cannot_take_are_dropped_instead_of_failing_the_job(samples, tmp_path,
                                                                            monkeypatch, caplog):
    monkeypatch.setattr(media, "subtitle_plan", lambda path: [(99, "copy")])      # no such track
    dest = str(tmp_path / "fallback - Highlights.mkv")
    with caplog.at_level(logging.WARNING, logger="highlightarr.media"):
        detector.process(samples["mp4"], dest, CFG)
    assert "retrying without them" in caplog.text
    _check_highlights(samples["mp4"], dest)


def test_relative_source_paths_work(samples, tmp_path, monkeypatch):
    monkeypatch.chdir(os.path.dirname(samples["mp4"]))
    dest = str(tmp_path / "relative - Highlights.mkv")
    detector.process(os.path.basename(samples["mp4"]), dest, CFG)
    assert os.path.exists(dest)
