"""Which files get picked up, and the explanations shown on the Files page."""
import os

from sqlmodel import Session

from app import config, sources, worker
from app.db import engine
from app.models import Profile, Workflow


def _touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_find_sources_filters(media, tmp_path):
    src, dest = str(tmp_path / "in"), str(tmp_path / "in" / "out")
    for rel in ("a.mkv", "B.MKV", "sub/c.mkv", "notes.txt", ".hidden.mkv", "a - Highlights.mkv",
                "out/already.mkv", "@eaDir/thumb.mkv", ".git/x.mkv"):
        _touch(f"{src}/{rel}")
    recursive = [os.path.relpath(p, src) for p in sources.find_sources(src, dest, True)]
    assert recursive == ["B.MKV", "a.mkv", "sub/c.mkv"]
    flat = [os.path.relpath(p, src) for p in sources.find_sources(src, dest, False)]
    assert flat == ["B.MKV", "a.mkv"]
    assert sources.find_sources(str(tmp_path / "missing"), dest, True) == []


def test_skip_reasons():
    assert sources.skip_reason("race.mkv") is None
    assert sources.skip_reason("RACE.MKV") is None
    assert sources.skip_reason("race - Highlights.mkv") == sources.OUTPUT
    assert sources.skip_reason(".race.mkv") == sources.HIDDEN
    assert sources.skip_reason("race.mp4") is None          # supported now
    assert sources.skip_reason("race.vob") == sources.UNSUPPORTED
    assert sources.skip_reason("race.srt") == sources.OTHER
    assert sources.skip_reason("race.mkv.part") == sources.OTHER


def _report(tmp_path, *, seen=None, done=None, processing=None, enabled=True, recursive=True,
            next_scan_at=None, limit=200):
    return sources.describe(
        source=str(tmp_path / "in"), dest=str(tmp_path / "out"), recursive=recursive, enabled=enabled,
        has_profile=True, seen=seen or {}, done=done or {}, processing=processing, settle_sec=300,
        poll_sec=60, next_scan_at=next_scan_at, now=1000.0, limit=limit)


def test_every_file_gets_a_reason_that_matches_what_the_worker_does(tmp_path):
    src = str(tmp_path / "in")
    for name in ("ready", "waiting", "unseen", "grown", "failed", "processed", "has_output", "busy"):
        _touch(f"{src}/{name}.mkv", size=10)
    for name in ("x - Highlights.mkv", "clip.vob", ".hidden.mkv", "notes.nfo", "sub/deep.mkv"):
        _touch(f"{src}/{name}")
    _touch(str(tmp_path / "out" / "has_output - Highlights.mkv"))
    seen = {f"{src}/ready.mkv": (10, 600.0),       # unchanged for 400 s of 300 needed
            f"{src}/waiting.mkv": (10, 800.0),     # unchanged for 200 s
            f"{src}/grown.mkv": (5, 900.0)}        # was a different size at the last scan
    done = {f"{src}/failed.mkv": ("failed", "ffprobe could not read the file"),
            f"{src}/processed.mkv": ("success", "7.2 min")}
    report = _report(tmp_path, seen=seen, done=done, processing=f"{src}/busy.mkv")
    by_name = {f.name: f for f in report.files}

    def check(name, state, text):
        assert by_name[name].state == state, (name, by_name[name])
        assert text in by_name[name].reason, (name, by_name[name].reason)

    check("busy.mkv", sources.PROCESSING, "Being processed")
    check("ready.mkv", sources.READY, "after the current file")
    check("waiting.mkv", sources.WAITING, "3 min 20 s of the 5 min needed")
    check("unseen.mkv", sources.WAITING, "Not checked yet")
    check("grown.mkv", sources.WAITING, "size changed")
    assert by_name["failed.mkv"].state == sources.FAILED
    assert "ffprobe could not read the file" in by_name["failed.mkv"].reason
    assert by_name["processed.mkv"].state == sources.DONE
    assert by_name["has_output.mkv"].state == sources.SKIPPED
    assert "has_output - Highlights.mkv" in by_name["has_output.mkv"].reason
    assert "Not a supported video format" in by_name["clip.vob"].reason
    assert ".mkv, .mp4" in by_name["clip.vob"].reason
    assert "highlights file made by this app" in by_name["x - Highlights.mkv"].reason
    assert "Hidden file" in by_name[".hidden.mkv"].reason
    assert os.path.join("sub", "deep.mkv") in by_name
    assert "notes.nfo" not in by_name and report.ignored == 1       # counted, not listed
    # most useful first
    states = [f.state for f in report.files]
    assert states == sorted(states, key=list(sources._ORDER).index)


def test_ready_without_a_current_job_and_scan_timing(tmp_path):
    _touch(str(tmp_path / "in" / "a.mkv"))
    seen = {str(tmp_path / "in" / "a.mkv"): (10, 100.0)}
    report = _report(tmp_path, seen=seen, next_scan_at=1025.0)
    assert report.files[0].reason == "Ready. It will be processed at the next scan."
    assert report.scan_note == "Scans every 1 min; next scan in about 25 s."
    assert "first scan starts shortly" in _report(tmp_path).scan_note
    busy = _report(tmp_path, processing="/somewhere/else.mkv")
    assert "next scan starts when it is done" in busy.scan_note


def test_folder_and_workflow_level_notes(tmp_path):
    missing = sources.describe(
        source=str(tmp_path / "nope"), dest=str(tmp_path / "out"), recursive=True, enabled=False,
        has_profile=False, seen={}, done={}, processing=None, settle_sec=300, poll_sec=60,
        next_scan_at=None)
    joined = " ".join(missing.notes)
    assert "disabled" in joined and "no profile" in joined and "does not exist" in joined
    assert missing.folder_ok is False and missing.files == []

    _touch(str(tmp_path / "in" / "top.mkv"))
    _touch(str(tmp_path / "in" / "season1" / "e1.mkv"))
    _touch(str(tmp_path / "in" / "season1" / "e2.mkv"))
    _touch(str(tmp_path / "in" / "@eaDir" / "thumb.mkv"))
    flat = _report(tmp_path, recursive=False)
    assert [f.name for f in flat.files] == ["top.mkv"]
    assert any("2 video files in subfolders are ignored" in n and "Scan subfolders" in n for n in flat.notes)
    assert not _report(tmp_path, recursive=True).notes
    (tmp_path / "in" / "season1" / "e2.mkv").unlink()
    assert any("1 video file in subfolders is ignored" in n for n in _report(tmp_path, recursive=False).notes)


def test_long_lists_are_cut_with_a_count(tmp_path):
    for i in range(7):
        _touch(str(tmp_path / "in" / f"f{i}.mkv"))
    report = _report(tmp_path, limit=5)
    assert len(report.files) == 5 and report.more == 2


def test_describe_never_changes_the_workers_bookkeeping(tmp_path):
    _touch(str(tmp_path / "in" / "a.mkv"))
    seen, done = {}, {}
    _report(tmp_path, seen=seen, done=done)
    assert seen == {} and done == {}


def test_files_page(client, media, tmp_path):
    src, dest = f"{media}/in", f"{media}/out"
    _touch(f"{src}/Race.mkv")
    _touch(f"{src}/Clip.vob")
    with Session(engine) as s:
        profile = Profile(name="Racing")
        s.add(profile)
        s.commit()
        s.refresh(profile)
        wf = Workflow(name="races", source_path=src, dest_path=dest, profile_id=profile.id)
        s.add(wf)
        s.commit()
        s.refresh(wf)
        wf_id = wf.id
    page = client.get(f"/workflows/{wf_id}/files")
    assert page.status_code == 200
    assert "Race.mkv" in page.text and "Not checked yet" in page.text
    assert "Clip.vob" in page.text and "Not a supported video format" in page.text
    assert "supported extension (.mkv, .mp4" in page.text
    assert f"/workflows/{wf_id}/files" in client.get("/workflows").text
    assert f"/workflows/{wf_id}/files" in client.get(f"/workflows/{wf_id}/edit").text
    assert client.get("/workflows/999/files").status_code == 404

    # after a scan the page reflects the worker's state
    worker.ready_sources(worker.WorkflowSnapshot(
        id=wf_id, name="races", source_path=src, dest_path=dest, recursive=True,
        cut=None, delete_original=False))
    assert "Waiting for the file to stop changing" in client.get(f"/workflows/{wf_id}/files").text


def test_retry_buttons_on_the_files_page(client, media):
    src, dest = f"{media}/in", f"{media}/out"
    for name in ("bad.mkv", "worse.mkv", "fine.mkv"):
        _touch(f"{src}/{name}")
    with Session(engine) as s:
        profile = Profile(name="Racing")
        s.add(profile)
        s.commit()
        s.refresh(profile)
        wf = Workflow(name="races", source_path=src, dest_path=dest, profile_id=profile.id)
        s.add(wf)
        s.commit()
        s.refresh(wf)
        wf_id = wf.id
    worker._done[wf_id] = {f"{src}/bad.mkv": ("failed", "ffprobe could not read the file"),
                           f"{src}/worse.mkv": ("failed", "output duration looks wrong"),
                           f"{src}/fine.mkv": ("success", "7.2 min")}
    url = f"/workflows/{wf_id}/files"
    page = client.get(url).text
    assert page.count(">Retry</button>") == 2 and "Retry all failed (2)" in page   # failed rows only

    r = client.post(f"{url}/retry", data={"name": "bad.mkv"})
    assert r.status_code == 303 and r.headers["location"] == url
    assert list(worker._done[wf_id]) == [f"{src}/worse.mkv", f"{src}/fine.mkv"]
    assert page.count("failed") > client.get(url).text.count("failed")
    assert "Retry all failed (1)" in client.get(url).text

    assert client.post(f"{url}/retry", data={}).status_code == 303                  # all failed
    assert list(worker._done[wf_id]) == [f"{src}/fine.mkv"]
    assert "Retry all failed" not in client.get(url).text and ">Retry</button>" not in client.get(url).text
    assert client.post("/workflows/999/files/retry", data={}).status_code == 404


def test_destination_can_be_the_source_folder(tmp_path):
    """Output lands next to the originals and is never mistaken for a new recording."""
    src = str(tmp_path / "shows")
    for rel in ("a.mkv", "sub/b.mkv", "a - Highlights.mkv", "sub/b - Highlights.mkv"):
        _touch(f"{src}/{rel}")
    for recursive, expected in ((True, ["a.mkv", "sub/b.mkv"]), (False, ["a.mkv"])):
        found = [os.path.relpath(p, src) for p in sources.find_sources(src, src, recursive)]
        assert found == expected, recursive
    assert sources.dest_for(src, src, f"{src}/sub/b.mkv") == f"{src}/sub/b - Highlights.mkv"


def test_files_page_for_a_workflow_that_saves_next_to_the_originals(tmp_path):
    src = str(tmp_path / "shows")
    _touch(f"{src}/a.mkv")
    _touch(f"{src}/a - Highlights.mkv")
    _touch(f"{src}/b.mkv")
    report = sources.describe(source=src, dest=src, recursive=True, enabled=True, has_profile=True,
                              seen={}, done={}, processing=None, settle_sec=300, poll_sec=60,
                              next_scan_at=None, now=1000.0)
    by_name = {f.name: f for f in report.files}
    assert "highlights file already exists" in by_name["a.mkv"].reason
    assert "Not checked yet" in by_name["b.mkv"].reason
    assert "made by this app" in by_name["a - Highlights.mkv"].reason


def test_every_supported_extension_is_picked_up_and_output_is_always_mkv(tmp_path):
    src = str(tmp_path / "in")
    names = [f"Race{ext}" for ext in config.VIDEO_EXTENSIONS] + ["Upper.MP4", "Other.VOB", "Other.iso"]
    for name in names:
        _touch(f"{src}/{name}")
    found = [os.path.basename(p) for p in sources.find_sources(src, str(tmp_path / "out"), False)]
    assert sorted(found) == sorted([f"Race{ext}" for ext in config.VIDEO_EXTENSIONS] + ["Upper.MP4"])
    assert sources.dest_for(src, "/out", f"{src}/Race.ts") == "/out/Race - Highlights.mkv"
    assert sources.dest_for(src, "/out", f"{src}/sub/Match.m2ts") == "/out/sub/Match - Highlights.mkv"
