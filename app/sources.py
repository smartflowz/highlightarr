"""Which files in a workflow's source folder get picked up, and why (not).

`skip_reason`, `iter_files`, `find_sources` and `dest_for` are the rules the worker
runs on. `describe` explains the same rules for the Files page; it only reads the
worker's bookkeeping, never changes it, so looking at the page cannot affect anything.
"""
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from . import config, paths
from .models import STATUS_SUCCESS

# Why a file name is not a candidate.
HIDDEN, OUTPUT, UNSUPPORTED, OTHER = "hidden", "output", "unsupported", "other"

# Video formats people may drop in that are not supported: ignored, but worth explaining.
UNSUPPORTED_VIDEO = (".vob", ".iso", ".ogv", ".divx", ".rm", ".rmvb")

# File states shown on the Files page.
PROCESSING, READY, WAITING, FAILED, DONE, SKIPPED = (
    "processing", "ready", "waiting", "failed", "done", "skipped")
_ORDER = {s: i for i, s in enumerate((PROCESSING, READY, WAITING, FAILED, DONE, SKIPPED))}


def skip_reason(name: str) -> str | None:
    """None if a file with this name is a candidate, otherwise why it is ignored."""
    lower = name.lower()
    if name.startswith("."):
        return HIDDEN
    if lower.endswith(config.VIDEO_EXTENSIONS):
        # Never pick up our own output, even if the folders overlap.
        stem = lower.rsplit(".", 1)[0]
        return OUTPUT if stem.endswith(config.OUTPUT_SUFFIX.lower()) else None
    return UNSUPPORTED if lower.endswith(UNSUPPORTED_VIDEO) else OTHER


def iter_files(source: str, dest: str, recursive: bool) -> Iterator[str]:
    """Every file the scan looks at: the source folder, and its subfolders when
    `recursive` (hidden folders and the destination folder are skipped)."""
    skip = os.path.realpath(dest)
    if recursive:
        for root, dirs, names in os.walk(source):
            dirs[:] = [d for d in dirs
                       if not d.startswith(paths.HIDDEN_PREFIXES)
                       and os.path.realpath(os.path.join(root, d)) != skip]
            for name in names:
                yield os.path.join(root, name)
    else:
        for name in os.listdir(source):
            path = os.path.join(source, name)
            if os.path.isfile(path):
                yield path


def find_sources(source: str, dest: str, recursive: bool) -> list[str]:
    """All candidate files under `source` (blocking — run in a thread)."""
    if not os.path.isdir(source):
        return []
    return sorted(p for p in iter_files(source, dest, recursive)
                  if skip_reason(os.path.basename(p)) is None)


def dest_for(source: str, dest: str, src: str) -> str:
    name, _ = os.path.splitext(os.path.relpath(src, source))
    return os.path.join(dest, f"{name}{config.OUTPUT_SUFFIX}{config.OUTPUT_EXTENSION}")


def human_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


@dataclass(frozen=True)
class FileStatus:
    name: str      # path relative to the source folder
    state: str     # one of the states above
    reason: str    # a sentence a person can act on


@dataclass
class SourceReport:
    notes: list[str] = field(default_factory=list)     # problems with the workflow or folder
    folder_ok: bool = True                             # False if the source folder is missing
    scan_note: str = ""                                # when the next scan happens
    files: list[FileStatus] = field(default_factory=list)
    ignored: int = 0                                   # other files, not listed
    more: int = 0                                      # listed files beyond the limit


def _count_below(source: str, dest: str, cap: int = 5000) -> int:
    """Candidate files in subfolders (used when subfolders are not scanned)."""
    skip, found, looked = os.path.realpath(dest), 0, 0
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs
                   if not d.startswith(paths.HIDDEN_PREFIXES)
                   and os.path.realpath(os.path.join(root, d)) != skip]
        if root == source:
            continue
        for name in names:
            looked += 1
            found += skip_reason(name) is None
        if looked >= cap:
            break
    return found


def _candidate_status(path: str, *, source: str, dest: str, seen: dict, done: dict,
                      processing: str | None, settle_sec: int, now: float,
                      waiting_for_window: str | None = None) -> FileStatus | None:
    """The same checks, in the same order, as the worker's `ready_sources`."""
    name = os.path.relpath(path, source)
    if path == processing:
        return FileStatus(name, PROCESSING, "Being processed right now.")
    if path in done:
        status, message = done[path]
        if status == STATUS_SUCCESS:
            return FileStatus(name, DONE,
                              "Already processed (see Activity). It is not processed again unless "
                              "it is removed and added back, or the app restarts.")
        return FileStatus(name, FAILED, f"Failed: {message}. It is not retried automatically: "
                                        "use Retry once the problem is fixed.")
    existing = dest_for(source, dest, path)
    if os.path.exists(existing):
        return FileStatus(name, SKIPPED, "A highlights file already exists "
                                         f"({os.path.relpath(existing, dest)}). Delete that file to have "
                                         "this one processed again.")
    try:
        size = os.path.getsize(path)
    except OSError:
        return None                                    # vanished while we looked
    previous = seen.get(path)
    if previous is None:
        return FileStatus(name, WAITING, "Not checked yet. It is first seen at the next scan; then its size "
                                         f"must stay the same for {human_duration(settle_sec)}.")
    if previous[0] != size:
        return FileStatus(name, WAITING, "Its size changed since the last scan, so it may still be copying. "
                                         "The waiting time starts again.")
    unchanged = now - previous[1]
    if unchanged < settle_sec:
        return FileStatus(name, WAITING,
                          "Waiting for the file to stop changing: unchanged for "
                          f"{human_duration(unchanged)} of the {human_duration(settle_sec)} needed.")
    if waiting_for_window:
        return FileStatus(name, READY, "Ready, but processing only runs between "
                                       f"{waiting_for_window}. It starts when that window opens.")
    return FileStatus(name, READY, "Ready. It will be processed "
                                   + ("after the current file." if processing else "at the next scan."))


def describe(*, source: str, dest: str, recursive: bool, enabled: bool, has_profile: bool,
             seen: dict, done: dict, processing: str | None, settle_sec: int, poll_sec: int,
             next_scan_at: float | None, now: float | None = None, limit: int = 200,
             window: str | None = None, window_open: bool = True) -> SourceReport:
    now = time.time() if now is None else now
    report = SourceReport()
    if not enabled:
        report.notes.append("This workflow is disabled, so no files are picked up until you enable it.")
    if not has_profile:
        report.notes.append("This workflow has no profile, so nothing is processed.")
    if not os.path.isdir(source):
        report.notes.append(f"The source folder {source} does not exist or is not mounted.")
        report.folder_ok = False
        return report

    if processing:
        report.scan_note = "A file is being processed; the next scan starts when it is done."
    elif next_scan_at is None:
        report.scan_note = (f"Scans every {human_duration(poll_sec)}. "
                            "The first scan starts shortly after the app starts.")
    else:
        report.scan_note = (f"Scans every {human_duration(poll_sec)}; "
                            f"next scan in about {human_duration(next_scan_at - now)}.")

    if window:
        report.scan_note += (f" Processing only runs between {window} (local time); "
                             + ("that window is open now." if window_open else "that window is closed now."))
    closed = window if window and not window_open else None
    files: list[FileStatus] = []
    try:
        for path in iter_files(source, dest, recursive):
            name = os.path.basename(path)
            reason = skip_reason(name)
            if reason is None:
                status = _candidate_status(path, source=source, dest=dest, seen=seen, done=done,
                                           processing=processing, settle_sec=settle_sec, now=now,
                                           waiting_for_window=closed)
                if status:
                    files.append(status)
                continue
            rel = os.path.relpath(path, source)
            lower = name.lower()
            if reason == OUTPUT:
                files.append(FileStatus(
                    rel, SKIPPED, "Looks like a highlights file made by this app (the name "
                                  f"ends with \"{config.OUTPUT_SUFFIX}\"), so it is ignored."))
            elif reason == UNSUPPORTED:
                files.append(FileStatus(
                    rel, SKIPPED, "Not a supported video format. Supported: "
                                  + ", ".join(config.VIDEO_EXTENSIONS) + "."))
            elif reason == HIDDEN and lower.endswith(config.VIDEO_EXTENSIONS):
                files.append(FileStatus(rel, SKIPPED,
                                        "Hidden file (the name starts with a dot), so it is ignored."))
            else:
                report.ignored += 1
    except OSError as exc:
        report.notes.append(f"The source folder could not be read: {exc.strerror or exc}.")

    if not recursive:
        below = _count_below(source, dest)
        if below:
            plural = below != 1
            report.notes.append(f"{below} video file{'s' if plural else ''} in subfolders "
                                f"{'are' if plural else 'is'} ignored because "
                                "\"Scan subfolders too\" is off for this workflow.")
    files.sort(key=lambda f: (_ORDER[f.state], f.name.lower()))
    report.more = max(0, len(files) - limit)
    report.files = files[:limit]
    return report
