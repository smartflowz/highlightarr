"""Being gentle on the machine (priority, threads, time window) and hardware encoding."""
import asyncio

import pytest

from app import media, settings, sources, worker


def test_window_logic():
    assert settings.in_window(3) is True                                  # no window configured
    for start, end, inside, outside in ((9, 17, (9, 12, 16), (8, 17, 23)),
                                        (22, 7, (22, 23, 0, 6), (7, 12, 21))):
        for hour in inside:
            assert _in_window(start, end, hour), (start, end, hour)
        for hour in outside:
            assert not _in_window(start, end, hour), (start, end, hour)
    assert _window(5, 5) is None and _window(-1, -1) is None and _window(22, -1) is None


def _window(start, end):
    original = settings.get
    settings.get = lambda key: {"window_start": start, "window_end": end}.get(key, original(key))
    try:
        return settings.window()
    finally:
        settings.get = original


def _in_window(start, end, hour):
    original = settings.get
    settings.get = lambda key: {"window_start": start, "window_end": end}.get(key, original(key))
    try:
        return settings.in_window(hour)
    finally:
        settings.get = original


def test_settings_validation_for_the_processing_options():
    values, errors = settings.parse_form({"ffmpeg_nice": "5", "ffmpeg_threads": "2",
                                          "window_start": "22", "window_end": "7"})
    assert not errors and values["ffmpeg_nice"] == 5 and values["window_end"] == 7
    assert "Set both" in settings.parse_form({"window_start": "22"})[1][0]
    assert settings.parse_form({"ffmpeg_nice": "25"})[1]                  # nice is 0-19
    values, errors = settings.parse_form({"window_start": "24", "window_end": "1"})   # hours are 0-23
    assert errors == ["Process only from must be between 0 and 23."]                  # reported once
    assert settings.parse_form({"window_start": "x"})[1] == ["Process only from must be a whole number."]


def test_processing_settings_page_roundtrip(client):
    page = client.get("/settings").text
    for text in ("Processing", "Processing priority", "ffmpeg threads", "Process only from"):
        assert text in page
    data = {"ffmpeg_nice": "5", "ffmpeg_threads": "2", "window_start": "22", "window_end": "7"}
    assert client.post("/settings/save", data=data).status_code == 303
    try:
        assert (settings.nice(), settings.threads(), settings.window()) == (5, 2, (22, 7))
        assert "22:00–07:00" in client.get("/about").text
    finally:
        client.post("/settings/save", data={})
    assert settings.window() is None


def test_gentle_wraps_the_command_in_nice_and_ionice(monkeypatch):
    command = ["ffmpeg", "-i", "x"]
    monkeypatch.setattr(media.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert media.gentle(command, media.Runtime(nice=10)) == [
        "nice", "-n", "10", "ionice", "-c", "2", "-n", "7", *command]
    assert media.gentle(command, media.Runtime(nice=0)) == command         # normal priority: untouched
    assert media.gentle(command, None) == command
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.gentle(command, media.Runtime(nice=10)) == command        # tools missing: still runs


def test_thread_limit_and_video_arguments():
    assert media._threads(media.Runtime(threads=2)) == ["-threads", "2"]
    assert media._threads(media.Runtime(threads=0)) == [] and media._threads(None) == []
    assert media.video_args(False, "software", "veryfast", 21, "/dev/dri/x") == ([], ["-c", "copy"])
    before, after = media.video_args(True, "software", "fast", 19, "/dev/dri/x")
    assert before == [] and after[:6] == ["-c:v", "libx264", "-preset", "fast", "-crf", "19"]
    before, after = media.video_args(True, "hardware", "fast", 23, "/dev/dri/renderD129")
    assert before == ["-vaapi_device", "/dev/dri/renderD129"]
    assert "h264_vaapi" in after and after[after.index("-qp") + 1] == "23" and "libx264" not in after


def test_hardware_status_explains_what_is_missing(monkeypatch):
    monkeypatch.setattr(media, "_hw_cache", {})
    works, why = media.hardware_status("/dev/dri/not-here")
    assert works is False and "/dev/dri/not-here not found" in why and "/dev/dri" in why
    assert media.hardware_status("/dev/dri/not-here") == (works, why)       # remembered for a minute


def test_unknown_encoder_is_rejected():
    with pytest.raises(media.ProcessingError, match="Unknown encoder"):
        media.cut("/nonexistent.mkv", "/tmp/x.mkv", [(0.0, 1.0)], 0, True, encoder="quantum")


def _snapshot(tmp_path):
    return worker.WorkflowSnapshot(id=77, name="t", source_path=str(tmp_path), dest_path=str(tmp_path / "o"),
                                   recursive=True, cut=None, delete_original=False)


def test_no_new_file_is_started_outside_the_processing_window(tmp_path, monkeypatch):
    started = []

    async def fake_run(wf, src):
        started.append(src)

    (tmp_path / "a.mkv").write_bytes(b"x")
    monkeypatch.setattr(worker, "_load_workflows", lambda: [_snapshot(tmp_path)])
    monkeypatch.setattr(worker, "ready_sources", lambda wf: [str(tmp_path / "a.mkv")])
    monkeypatch.setattr(worker, "_run_job", fake_run)
    monkeypatch.setattr(worker, "_window_open", lambda: False)
    asyncio.run(worker.scan_once())
    assert started == []
    monkeypatch.setattr(worker, "_window_open", lambda: True)
    asyncio.run(worker.scan_once())
    assert started == [str(tmp_path / "a.mkv")]


def test_files_page_explains_a_closed_processing_window(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.mkv").write_bytes(b"x")
    report = sources.describe(source=str(src), dest=str(tmp_path / "out"), recursive=True, enabled=True,
                              has_profile=True, seen={str(src / "a.mkv"): (1, 0.0)}, done={}, processing=None,
                              settle_sec=300, poll_sec=60, next_scan_at=None, now=1000.0,
                              window="22:00–07:00", window_open=False)
    assert "22:00–07:00" in report.scan_note and "closed" in report.scan_note
    [file] = report.files
    assert file.state == sources.READY and "only runs between 22:00–07:00" in file.reason


def test_invalid_hour_on_the_settings_page_shows_a_message_not_an_error(client):
    response = client.post("/settings/save", data={"window_start": "24", "window_end": "1"})
    assert response.status_code == 400 and "must be between 0 and 23" in response.text
