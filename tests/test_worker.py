import os

from app import settings, worker


def _touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def _snapshot(tmp_path, wf_id=1):
    from app.detector import CutConfig
    return worker.WorkflowSnapshot(
        id=wf_id, name="t", source_path=str(tmp_path / "in"), dest_path=str(tmp_path / "out"),
        recursive=True, cut=CutConfig(target_fraction=0.25), delete_original=False)


def test_file_is_ready_only_after_size_is_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "settle_sec", lambda: 0)
    wf = _snapshot(tmp_path)
    f = str(tmp_path / "in" / "race.mkv")
    _touch(f, 10)
    assert worker.ready_sources(wf) == []          # first sighting
    assert worker.ready_sources(wf) == [f]         # unchanged since
    _touch(f, 20)                                  # still being copied
    assert worker.ready_sources(wf) == []
    assert worker.ready_sources(wf) == [f]


def test_finished_and_vanished_files_are_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "settle_sec", lambda: 0)
    wf = _snapshot(tmp_path)
    f = str(tmp_path / "in" / "race.mkv")
    _touch(f)
    worker.ready_sources(wf)
    assert worker.ready_sources(wf) == [f]
    worker._done[wf.id][f] = ("failed", "boom")    # handled (e.g. failed)
    assert worker.ready_sources(wf) == []
    os.remove(f)
    assert worker.ready_sources(wf) == []
    assert worker._seen[wf.id] == {} and worker._done[wf.id] == {}   # bookkeeping pruned
    _touch(f)                                      # same name returns: retried
    worker.ready_sources(wf)
    assert worker.ready_sources(wf) == [f]


def test_existing_output_is_not_redone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "settle_sec", lambda: 0)
    wf = _snapshot(tmp_path)
    _touch(str(tmp_path / "in" / "race.mkv"))
    _touch(str(tmp_path / "out" / "race - Highlights.mkv"))
    worker.ready_sources(wf)
    assert worker.ready_sources(wf) == []


def test_scan_skips_files_that_vanished_while_waiting(monkeypatch, tmp_path):
    import asyncio

    wf = worker.WorkflowSnapshot(id=1, name="t", source_path=str(tmp_path), dest_path=str(tmp_path / "o"),
                                 recursive=True, cut=None, delete_original=False)
    kept = tmp_path / "kept.mkv"
    kept.write_bytes(b"x")
    ran = []

    async def fake_run(w, src):
        ran.append(src)

    monkeypatch.setattr(worker, "_load_workflows", lambda: [wf])
    monkeypatch.setattr(worker, "ready_sources", lambda w: [str(tmp_path / "gone.mkv"), str(kept)])
    monkeypatch.setattr(worker, "_run_job", fake_run)
    asyncio.run(worker.scan_once())
    assert ran == [str(kept)]


def test_retry_failed_makes_files_eligible_again(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "settle_sec", lambda: 0)
    wf = _snapshot(tmp_path)
    src = str(tmp_path / "in")
    files = {name: f"{src}/{name}.mkv" for name in ("bad1", "bad2", "good")}
    for path in files.values():
        _touch(path)
    worker.ready_sources(wf)                                  # first sighting
    worker._done[wf.id] = {files["bad1"]: ("failed", "boom"), files["bad2"]: ("failed", "boom"),
                           files["good"]: ("success", "ok")}
    assert worker.ready_sources(wf) == []                     # all handled

    assert worker.retry_failed(wf.id, src, "bad1.mkv") == 1   # one file, by its name
    assert worker.ready_sources(wf) == [files["bad1"]]
    assert worker.retry_failed(wf.id, src, "good.mkv") == 0   # only failed files can be retried
    assert worker.retry_failed(wf.id, src, "../bad2.mkv") == 0
    assert worker.retry_failed(wf.id, src, "nope.mkv") == 0
    assert worker.retry_failed(wf.id, src) == 1               # all failed ones (bad2; bad1 is already reset)
    assert sorted(worker.ready_sources(wf)) == [files["bad1"], files["bad2"]]
    assert worker._done[wf.id] == {files["good"]: ("success", "ok")}
    assert worker.retry_failed(999, src) == 0                 # unknown workflow: nothing happens
