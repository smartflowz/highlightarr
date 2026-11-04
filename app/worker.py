"""Background loop: scan the enabled workflows, process files that have
settled, record the outcome in the activity list."""
import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from . import config, detector, media, settings, sources
from .db import engine
from .detector import CutConfig
from .media import ProcessingError
from .models import STATUS_FAILED, STATUS_SUCCESS, ActivityEntry, Workflow

log = logging.getLogger("highlightarr.worker")

SHUTDOWN_GRACE_SEC = 8   # how long a running job may take to stop (docker waits 10 s)


@dataclass(frozen=True)
class WorkflowSnapshot:
    """Plain copy of a workflow + its profile, so no ORM objects cross into
    worker threads or live across a long-running job."""
    id: int
    name: str
    source_path: str
    dest_path: str
    recursive: bool
    cut: CutConfig
    delete_original: bool


# In-memory only — deliberately not persisted. A restart resets the "is this
# file still being copied" clock, which is safe (just means a wait).
_seen: dict[int, dict[str, tuple[int, float]]] = {}   # workflow id -> path -> (size, first seen)
_done: dict[int, dict[str, tuple[str, str]]] = {}     # workflow id -> path -> (status, message) once handled

# When the next scan starts (None until the first one is scheduled). Read by the Files page.
_next_scan_at: float | None = None

# The file being processed right now, for the live-progress UI. Replaced as a
# whole (never mutated), so the web thread can read it without a lock.
_current: dict | None = None


def get_current() -> dict | None:
    current = _current
    if current is None:
        return None
    return {**current, "elapsed_sec": int(time.time() - current["started_at"])}


def _progress_callback(base: dict):
    def callback(stage: str, fraction: float | None):
        global _current
        _current = {**base, "stage": stage, "fraction": fraction}
    return callback


# ---------- finding work ----------

def _settled(seen: dict[str, tuple[int, float]], path: str) -> bool:
    """True once the file's size has been unchanged for the settle time."""
    try:
        size = os.path.getsize(path)
    except OSError:               # vanished between listing and now
        seen.pop(path, None)
        return False
    now = time.time()
    previous = seen.get(path)
    if previous is None or previous[0] != size:
        seen[path] = (size, now)
        return False
    return now - previous[1] >= settings.settle_sec()


def ready_sources(wf: WorkflowSnapshot) -> list[str]:
    """Files to process now (blocking — run in a thread)."""
    candidates = sources.find_sources(wf.source_path, wf.dest_path, wf.recursive)
    seen = _seen.setdefault(wf.id, {})
    done = _done.setdefault(wf.id, {})
    present = set(candidates)
    for stale in [p for p in seen if p not in present]:
        del seen[stale]
    for gone in [p for p in done if p not in present]:   # a file that vanishes and returns is retried
        del done[gone]
    return [src for src in candidates
            if src not in done
            and not os.path.exists(sources.dest_for(wf.source_path, wf.dest_path, src))
            and _settled(seen, src)]


def retry_failed(workflow_id: int, source: str, name: str | None = None) -> int:
    """Forget failed files so the next scan tries them again: the one called `name`
    (relative to the source folder), or all of them. Returns how many were reset."""
    done = _done.get(workflow_id, {})
    wanted = os.path.normpath(os.path.join(source, name)) if name else None
    targets = [path for path, (status, _) in list(done.items())
               if status == STATUS_FAILED and (wanted is None or os.path.normpath(path) == wanted)]
    for path in targets:
        done.pop(path, None)
    return len(targets)


def source_report(workflow) -> sources.SourceReport:
    """Why each file in a workflow's source folder will or will not be picked up
    (read-only: used by the Files page)."""
    current = _current
    return sources.describe(
        source=workflow.source_path, dest=workflow.dest_path, recursive=workflow.recursive,
        enabled=workflow.enabled, has_profile=workflow.profile is not None,
        seen=_seen.get(workflow.id, {}), done=_done.get(workflow.id, {}),
        processing=current["path"] if current else None,
        settle_sec=settings.settle_sec(), poll_sec=settings.poll_sec(), next_scan_at=_next_scan_at,
        window=settings.window_text() if settings.window() else None,
        window_open=settings.in_window(datetime.now().hour))


# ---------- doing work ----------

def _load_workflows() -> list[WorkflowSnapshot]:
    with Session(engine) as session:
        rows = session.exec(select(Workflow).where(col(Workflow.enabled))).all()
        return [WorkflowSnapshot(
                    id=w.id, name=w.name, source_path=w.source_path, dest_path=w.dest_path,
                    recursive=w.recursive, cut=CutConfig.from_profile(w.profile),
                    delete_original=w.profile.delete_original)
                for w in rows if w.profile]


def _record(wf: WorkflowSnapshot, src: str, dest: str | None, status: str, message: str) -> None:
    with Session(engine) as session:
        session.add(ActivityEntry(workflow_id=wf.id, workflow_name=wf.name, source_path=src,
                                 dest_path=dest, status=status, message=message))
        session.commit()
        stale = session.exec(select(ActivityEntry).order_by(col(ActivityEntry.created_at).desc())
                             .offset(config.ACTIVITY_KEEP)).all()
        for entry in stale:
            session.delete(entry)
        session.commit()


async def _run_job(wf: WorkflowSnapshot, src: str) -> None:
    global _current
    dest = sources.dest_for(wf.source_path, wf.dest_path, src)
    log.info("processing %s (workflow=%s)", src, wf.name)
    base = {"workflow": wf.name, "file": os.path.basename(src), "path": src,
            "started_at": time.time()}
    _current = {**base, "stage": "Starting", "fraction": None}
    cancel = threading.Event()
    try:
        try:
            job = asyncio.ensure_future(asyncio.to_thread(
                detector.process, src, dest, wf.cut, _progress_callback(base), cancel,
                runtime_settings()))
            try:
                result = await asyncio.shield(job)   # shield: cancelling us must not orphan the thread
            except asyncio.CancelledError:
                # Shutting down. Ask the thread to stop ffmpeg and remove its .part file,
                # and wait for it: uvicorn re-raises SIGTERM right after shutdown, which
                # would otherwise leave ffmpeg running on its own.
                log.info("shutting down: stopping %s", src)
                cancel.set()
                await asyncio.wait({job}, timeout=SHUTDOWN_GRACE_SEC)
                if job.done():
                    job.exception()                  # mark as retrieved
                raise
            message = (f"{result.duration_sec / 60:.1f} min, "
                       f"audio track {result.audio_track} used for detection")
            if result.encoder_note:
                message += f"; {result.encoder_note}"
            if wf.delete_original:
                try:
                    os.remove(src)
                    message += " (original deleted)"
                except OSError as exc:
                    message += f" (could not delete the original: {exc.strerror or exc})"
            status = STATUS_SUCCESS
            log.info("OK %s", dest)
        except ProcessingError as exc:
            log.warning("failed %s: %s", src, exc)
            status, message, dest = STATUS_FAILED, str(exc), None
        except Exception as exc:
            log.exception("failed %s", src)
            status, message, dest = STATUS_FAILED, f"unexpected error: {exc}", None
        _done.setdefault(wf.id, {})[src] = (status, message)
        _record(wf, src, dest, status, message)
    finally:
        # Cleared only after the activity row is committed, so a page that
        # reloads when the job disappears already sees the result.
        _current = None


_window_was_closed = False


def _window_open() -> bool:
    """Whether new files may be started now (see "Process only from/until" in Settings)."""
    global _window_was_closed
    is_open = settings.in_window(datetime.now().hour)
    if not is_open and not _window_was_closed:
        log.info("outside the processing window (%s): files wait until it opens", settings.window_text())
    _window_was_closed = not is_open
    return is_open


def runtime_settings() -> media.Runtime:
    return media.Runtime(nice=settings.nice(), threads=settings.threads(), vaapi_device=config.VAAPI_DEVICE)


async def scan_once() -> None:
    for wf in _load_workflows():
        for src in await asyncio.to_thread(ready_sources, wf):
            if not os.path.exists(src):   # removed while an earlier file was being processed
                continue
            if not _window_open():        # settle timers keep running; the job just waits
                return
            await _run_job(wf, src)


async def worker_loop() -> None:
    global _next_scan_at
    log.info("worker started: poll=%ss settle=%ss", settings.poll_sec(), settings.settle_sec())
    while True:
        try:
            await scan_once()
        except Exception:
            log.exception("scan cycle failed")
        # Sleep in 1s ticks and re-read the poll interval each time, so a
        # change on the Settings page takes effect without a restart.
        _next_scan_at = time.time() + settings.poll_sec()
        waited = 0
        while waited < settings.poll_sec():
            await asyncio.sleep(1)
            waited += 1
