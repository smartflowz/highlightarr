"""End-to-end with real ffmpeg (skipped when it is not installed)."""
import asyncio
import dataclasses
import os
import shutil
import subprocess

import pytest
from sqlmodel import Session, select

from app import detector, worker
from app.db import engine
from app.detector import CutConfig
from app.media import ProcessingError
from app.models import STATUS_FAILED, STATUS_SUCCESS, ActivityEntry

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """An 11-minute MKV with a loud burst every 150 s and a tagged audio track."""
    path = str(tmp_path_factory.mktemp("rec") / "race.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=5:duration=660",
         "-f", "lavfi", "-i", "sine=f=500:duration=660:sample_rate=8000,"
                              "volume='if(lt(mod(t,150),6),1,0.08)':eval=frame",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "5", "-c:a", "aac",
         "-metadata:s:a:0", "language=eng", "-shortest", path], check=True)
    return path


CFG = CutConfig(target_fraction=0.3, intro_sec=40, outro_sec=40, clip_before=10, clip_after=10,
                min_separation=60, languages="eng")


def test_process_cuts_to_target_and_reports_progress(recording, tmp_path):
    dest = str(tmp_path / "out" / "race - Highlights.mkv")
    events = []
    result = detector.process(recording, dest, CFG, lambda stage, f: events.append((stage, f)))
    assert os.path.exists(dest) and not os.path.exists(dest + ".part")
    assert 660 * 0.3 * 0.5 <= result.duration_sec <= 660 * 0.3 * 1.5
    assert result.audio_track == 0
    stages = {s for s, _ in events}
    assert stages == {"Analyzing audio", "Cutting highlights"}
    cutting = [f for s, f in events if s == "Cutting highlights"]
    assert cutting[0] == 0.0 and max(cutting) > 0.5 and all(0 <= f <= 1 for f in cutting)


def test_reencode_uses_the_chosen_preset_and_quality(recording, tmp_path, monkeypatch):
    seen = []
    real_popen = subprocess.Popen

    def spy(cmd, *args, **kwargs):
        seen.append(cmd)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    cfg = dataclasses.replace(CFG, reencode=True, video_preset="ultrafast", video_crf=30)
    dest = str(tmp_path / "out" / "re - Highlights.mkv")
    detector.process(recording, dest, cfg)
    cut_cmd = next(c for c in seen if "concat" in c)
    assert cut_cmd[cut_cmd.index("-preset") + 1] == "ultrafast"
    assert cut_cmd[cut_cmd.index("-crf") + 1] == "30"
    assert os.path.exists(dest)


def test_unknown_preset_is_rejected_before_ffmpeg_runs(recording, tmp_path):
    cfg = dataclasses.replace(CFG, reencode=True, video_preset="placebo")
    with pytest.raises(ProcessingError, match="Unknown encoder preset"):
        detector.process(recording, str(tmp_path / "x - Highlights.mkv"), cfg)


def test_failure_leaves_no_partial_output(tmp_path):
    bad = tmp_path / "broken.mkv"
    bad.write_bytes(b"this is not a video")
    dest = str(tmp_path / "out" / "broken - Highlights.mkv")
    with pytest.raises(ProcessingError):
        detector.process(str(bad), dest, CFG)
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")


def test_worker_job_records_success_and_deletes_original(recording, tmp_path):
    src = str(tmp_path / "in" / "race.mkv")
    os.makedirs(os.path.dirname(src))
    shutil.copy(recording, src)
    wf = worker.WorkflowSnapshot(id=7, name="races", source_path=str(tmp_path / "in"),
                                 dest_path=str(tmp_path / "out"), recursive=True, cut=CFG,
                                 delete_original=True)
    asyncio.run(worker._run_job(wf, src))
    with Session(engine) as s:
        entry = s.exec(select(ActivityEntry)).one()
    assert entry.status == STATUS_SUCCESS and entry.workflow_name == "races"
    assert "original deleted" in entry.message
    assert not os.path.exists(src)
    assert os.path.exists(entry.dest_path)
    assert worker.get_current() is None


def test_worker_job_records_failure_and_clears_current(tmp_path):
    src = tmp_path / "in" / "broken.mkv"
    src.parent.mkdir()
    src.write_bytes(b"nope")
    wf = worker.WorkflowSnapshot(id=8, name="bad", source_path=str(src.parent),
                                 dest_path=str(tmp_path / "out"), recursive=True, cut=CFG,
                                 delete_original=True)
    asyncio.run(worker._run_job(wf, str(src)))
    with Session(engine) as s:
        entry = s.exec(select(ActivityEntry)).one()
    assert entry.status == STATUS_FAILED and entry.dest_path is None
    assert src.exists()                                  # never delete after a failure
    assert str(src) in worker._done[8]                   # not retried every poll
    assert worker.get_current() is None


def test_cancel_stops_work_and_leaves_nothing_behind(recording, tmp_path):
    import threading

    from app.media import Cancelled
    cancel = threading.Event()
    cancel.set()
    dest = str(tmp_path / "out" / "race - Highlights.mkv")
    with pytest.raises(Cancelled):
        detector.process(recording, dest, CFG, cancel=cancel)
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")


def test_cancelling_the_job_task_stops_the_worker_thread_before_returning(monkeypatch, tmp_path):
    """Shutdown cancels the asyncio task; the thread must be told to stop, and finish."""
    import threading
    import time
    seen = {}

    def slow_process(src, dest, cfg, on_progress=None, cancel=None, runtime=None):
        seen["cancel"] = cancel
        seen["started"] = True
        cancel.wait(5)
        seen["stopped_by_cancel"] = cancel.is_set()
        time.sleep(0.3)                    # ffmpeg being stopped, .part file removed
        seen["cleaned_up"] = True
        raise ProcessingError("cancelled")

    monkeypatch.setattr(detector, "process", slow_process)
    wf = worker.WorkflowSnapshot(id=1, name="t", source_path=str(tmp_path), dest_path=str(tmp_path / "o"),
                                 recursive=True, cut=CFG, delete_original=False)

    async def scenario():
        task = asyncio.create_task(worker._run_job(wf, str(tmp_path / "a.mkv")))
        for _ in range(500):                       # up to 5 s; fail instead of hanging if it never starts
            if seen.get("started"):
                break
            await asyncio.sleep(0.01)
        assert seen.get("started"), "the job never started the worker thread"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen.get("cleaned_up")      # the task only ends once the thread is done

    asyncio.run(scenario())
    assert isinstance(seen["cancel"], threading.Event)
    assert seen["stopped_by_cancel"] is True
    assert worker.get_current() is None


def test_ffmpeg_messages_reach_the_log_and_the_failure_message(recording, tmp_path, monkeypatch, caplog):
    from app import media
    caplog.set_level("WARNING")
    monkeypatch.setattr(media, "video_args", lambda *args: ([], ["-c:v", "no_such_encoder"]))
    expected = r"ffmpeg failed \(exit code \d+\): Unknown encoder 'no_such_encoder'"
    with pytest.raises(ProcessingError, match=expected):
        media.cut(recording, str(tmp_path / "x.mkv"), [(0.0, 30.0)], 0, False)
    entry = next(r for r in caplog.records if "ffmpeg said" in r.getMessage())
    assert entry.levelname == "ERROR" and "no_such_encoder" in entry.getMessage()
    assert "race.mkv: cutting" in entry.getMessage()


def test_audio_analysis_failure_says_why(recording, caplog):
    from app import media
    caplog.set_level("WARNING")
    with pytest.raises(ProcessingError, match="audio decode failed: Stream map '0:a:5' matches no streams"):
        media.loudness(recording, 5, 660.0)                     # there is no 6th audio track
    assert any("audio analysis" in r.getMessage() for r in caplog.records)


def test_hardware_encoding_falls_back_to_software_when_there_is_no_gpu(recording, tmp_path, monkeypatch):
    from app import media
    monkeypatch.setattr(media, "_hw_cache", {})
    cfg = dataclasses.replace(CFG, reencode=True, video_encoder="hardware", video_preset="ultrafast")
    dest = str(tmp_path / "out" / "hw - Highlights.mkv")
    result = detector.process(recording, dest, cfg, runtime=media.Runtime(vaapi_device="/dev/dri/not-here"))
    assert os.path.exists(dest)
    assert result.encoder_note.startswith("software encoder, because the hardware encoder is not available")
    assert "/dev/dri/not-here not found" in result.encoder_note
