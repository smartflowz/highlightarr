"""All ffprobe / ffmpeg I/O. Nothing in here decides *what* to cut — see
detector.py for that."""
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

log = logging.getLogger("highlightarr.media")

SAMPLE_RATE = 8000
_BYTES_PER_SEC = SAMPLE_RATE * 2        # 16-bit mono

ProgressFn = Callable[[float], None]    # fraction 0..1


# x264 speed presets offered for re-encoding (slower = smaller file at the same quality).
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")
DEFAULT_PRESET = "veryfast"
CRF_RANGE = (15, 30)          # x264 quality: lower = better and bigger
DEFAULT_CRF = 21


# Subtitle formats that can be copied into the Matroska output as they are. MP4's text
# subtitles (mov_text) are converted to SRT; anything else (teletext, closed captions,
# unknown formats) is left out rather than risking a failed job.
SUBTITLE_COPY = frozenset({"subrip", "ass", "ssa", "webvtt", "text",
                           "hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"})
SUBTITLE_CONVERT = {"mov_text": "srt"}


# Where the video is encoded when re-encoding: on the CPU with x264, or on a GPU with VAAPI.
ENCODER_SOFTWARE, ENCODER_HARDWARE = "software", "hardware"
ENCODERS = (ENCODER_SOFTWARE, ENCODER_HARDWARE)
DEFAULT_VAAPI_DEVICE = "/dev/dri/renderD128"


@dataclass(frozen=True)
class Runtime:
    """How hard ffmpeg may work on this machine (see the Settings page)."""
    nice: int = 0                          # 0 = normal priority, 19 = lowest
    threads: int = 0                       # 0 = ffmpeg decides
    vaapi_device: str = DEFAULT_VAAPI_DEVICE


class CutInfo(NamedTuple):
    how: str                               # "copy", "software" or "hardware"
    fallback: str = ""                     # why hardware was wanted but software was used


class ProcessingError(RuntimeError):
    """A failure with a message that is safe to show in the UI."""


class Cancelled(ProcessingError):
    """The job was stopped on request (e.g. the app is shutting down)."""


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")


def gentle(command: list[str], runtime: Runtime | None) -> list[str]:
    """Run `command` at a low CPU priority, and with low disk priority where `ionice` exists.
    Both wrappers replace themselves with the command, so killing it still kills ffmpeg."""
    prefix: list[str] = []
    if runtime and runtime.nice > 0:
        if shutil.which("nice"):
            prefix += ["nice", "-n", str(runtime.nice)]
        if shutil.which("ionice"):
            prefix += ["ionice", "-c", "2", "-n", "7"]
    return prefix + command


def _threads(runtime: Runtime | None) -> list[str]:
    return ["-threads", str(runtime.threads)] if runtime and runtime.threads > 0 else []


def _run_quietly(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


@functools.cache
def has_encoder(name: str) -> bool:
    done = _run_quietly(["ffmpeg", "-hide_banner", "-encoders"])
    return bool(done and re.search(rf"\s{re.escape(name)}\s", done.stdout))


_HW_CACHE_SEC = 60.0
_hw_cache: dict[str, tuple[float, tuple[bool, str]]] = {}


def _probe_hardware(device: str) -> tuple[bool, str]:
    if not os.path.exists(device):
        return False, f"{device} not found (pass /dev/dri into the container)"
    if not has_encoder("h264_vaapi"):
        return False, "this ffmpeg has no h264_vaapi encoder"
    done = _run_quietly(["ffmpeg", "-nostdin", "-v", "error", "-vaapi_device", device,
                         "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2",
                         "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi",
                         "-frames:v", "1", "-f", "null", "-"])
    if done is None:
        return False, "the test encode could not be run"
    if done.returncode == 0:
        return True, ""
    lines = (done.stderr or "").strip().splitlines()
    detail = lines[-1] if lines else f"exit code {done.returncode}"
    if "ermission" in (done.stderr or ""):
        detail += " (the container user needs access to the device: see group_add in the README)"
    return False, f"a test encode failed: {detail}"


def hardware_status(device: str) -> tuple[bool, str]:
    """(works, reason it does not). Checked with a real one-frame encode and remembered for a
    minute, so a GPU that shows up later is noticed without a restart."""
    cached = _hw_cache.get(device)
    if cached and time.monotonic() - cached[0] < _HW_CACHE_SEC:
        return cached[1]
    result = _probe_hardware(device)
    _hw_cache[device] = (time.monotonic(), result)
    return result


def video_args(reencode: bool, encoder: str, preset: str, crf: int,
               device: str) -> tuple[list[str], list[str]]:
    """ffmpeg options for the video: (before the input, after it)."""
    if not reencode:
        return [], ["-c", "copy"]
    if encoder == ENCODER_HARDWARE:
        # Decode on the CPU (any source format works), encode on the GPU. The quality number
        # is a fixed quantizer here (CQP); it is used like the CRF of the software encoder.
        return (["-vaapi_device", device],
                ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-rc_mode", "CQP",
                 "-qp", str(crf), "-c:a", "aac"])
    return [], ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-c:a", "aac"]


def _ffprobe(args: list[str], path: str) -> str:
    try:
        return subprocess.run(["ffprobe", "-v", "error", *args, path],
                              capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout
    except FileNotFoundError:
        raise ProcessingError("ffprobe is not installed") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        raise ProcessingError(f"ffprobe could not read {os.path.basename(path)}"
                              + (f": {detail[-1]}" if detail else "")) from None


def duration(path: str) -> float:
    out = _ffprobe(["-show_entries", "format=duration", "-of", "csv=p=0"], path)
    try:
        return float(out.strip())
    except ValueError:
        raise ProcessingError("could not read the file's duration") from None


def start_time(path: str) -> float:
    """Where the file's own timeline starts. Usually 0, but recordings from a TV receiver
    often start hours in, and ffmpeg addresses cut points on that timeline."""
    out = _ffprobe(["-show_entries", "format=start_time", "-of", "csv=p=0"], path)
    try:
        return max(0.0, float(out.strip()))
    except ValueError:
        return 0.0


def subtitle_plan(path: str) -> list[tuple[int, str]]:
    """(stream index, "copy" or an encoder name) for each subtitle track worth keeping."""
    out = _ffprobe(["-select_streams", "s", "-show_entries", "stream=index,codec_name",
                    "-of", "json"], path)
    plan = []
    for stream in json.loads(out).get("streams", []):
        codec = stream.get("codec_name", "")
        if codec in SUBTITLE_COPY:
            plan.append((stream["index"], "copy"))
        elif codec in SUBTITLE_CONVERT:
            plan.append((stream["index"], SUBTITLE_CONVERT[codec]))
    return plan


def audio_languages(path: str) -> list[str]:
    """Language tag (lower-case, '' if untagged) of every audio stream."""
    out = _ffprobe(["-select_streams", "a", "-show_entries", "stream_tags=language",
                    "-of", "json"], path)
    streams = json.loads(out).get("streams", [])
    return [s.get("tags", {}).get("language", "").lower() for s in streams]


def _log_ffmpeg_output(label: str, text: str, failed: bool) -> str:
    """Put ffmpeg's own messages in the log (the Logs page shows them) and return the last lines,
    so a failure can say what actually went wrong."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    log.log(logging.ERROR if failed else logging.WARNING,
            "%s: ffmpeg said (last %d of %d lines):\n%s", label, min(len(lines), 20), len(lines),
            "\n".join(lines[-20:]))
    return _cause(lines)


# ffmpeg ends a failure with generic lines ("Error opening output files"); the cause is above them.
_GENERIC = re.compile(
    r"^(?:\[[^\]]*\]\s*)?"
    r"(Error (opening|parsing|selecting|initializing|while)|Conversion failed|To ignore this)",
    re.IGNORECASE)


def _cause(lines: list[str]) -> str:
    """The most informative line of ffmpeg's output: the first that is not a generic follow-up."""
    for line in lines:
        if not _GENERIC.match(line):
            return re.sub(r"^\[[^\]]*\]\s*", "", line)[:200]          # drop the "[vost#0:0 @ 0x...]" tag
    return lines[-1][:200]


def _read_back(err) -> str:
    err.seek(0)
    return err.read().decode("utf-8", errors="replace")


def loudness(path: str, track: int, total_sec: float, on_progress: ProgressFn | None = None,
             cancel: threading.Event | None = None, runtime: Runtime | None = None):
    """Per-second loudness of the speech band, decoded in one-minute chunks
    so memory use stays flat regardless of file length."""
    cmd = gentle(["ffmpeg", "-nostdin", "-v", "error", *_threads(runtime), "-i", path,
                  "-map", f"0:a:{track}", "-vn", "-af", "highpass=f=300,lowpass=f=3400",
                  "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"], runtime)
    chunks, leftover, decoded = [], b"", 0
    with tempfile.TemporaryFile() as err:       # ffmpeg's messages: a file, so they can never block it
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=err)
        try:
            while data := proc.stdout.read(_BYTES_PER_SEC * 60):
                _check(cancel)
                decoded += len(data)
                if on_progress and total_sec > 0:
                    on_progress(min(1.0, decoded / _BYTES_PER_SEC / total_sec))
                data = leftover + data
                usable = len(data) - len(data) % _BYTES_PER_SEC
                leftover = data[usable:]
                if usable:
                    samples = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32)
                    chunks.append(np.sqrt((samples.reshape(-1, SAMPLE_RATE) ** 2).mean(axis=1)))
            returncode = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
        text = _read_back(err)
    failed = returncode != 0 or not chunks
    last = _log_ffmpeg_output(f"{os.path.basename(path)}: audio analysis", text, failed)
    if failed:
        raise ProcessingError("audio decode failed" + (f": {last}" if last else ""))
    return np.concatenate(chunks)


def _run_ffmpeg(cmd: list[str], expected: float, on_progress: ProgressFn | None,
                cancel: threading.Event | None) -> tuple[int, bool, str]:
    """Run ffmpeg, relaying its progress. Returns (exit code, whether any output was written,
    what ffmpeg printed on stderr)."""
    # stdout carries ffmpeg's machine-readable progress; its messages go to a file.
    produced = False
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=err, text=True)
        try:
            for line in proc.stdout:
                _check(cancel)
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        done = int(value) / 1_000_000
                    except ValueError:
                        continue
                    produced = produced or done > 0
                    if on_progress and expected > 0:
                        on_progress(max(0.0, min(1.0, done / expected)))
            returncode = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
        return returncode, produced, _read_back(err)


def cut(src: str, dest: str, segments: list[tuple[float, float]], audio_track: int,
        reencode: bool, on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
        preset: str = DEFAULT_PRESET, crf: int = DEFAULT_CRF,
        encoder: str = ENCODER_SOFTWARE, runtime: Runtime | None = None) -> CutInfo:
    """Concatenate `segments` of `src` into `dest` (a Matroska file, whatever the source
    container is). Without `reencode` the streams are copied. With it the video is encoded by
    `encoder`: x264 using `preset` and `crf`, or the GPU (VAAPI) using `crf` as its quantizer.
    Hardware encoding quietly falls back to software when the GPU is missing or fails."""
    if preset not in X264_PRESETS:
        raise ProcessingError(f"Unknown encoder preset {preset!r}.")
    if encoder not in ENCODERS:
        raise ProcessingError(f"Unknown encoder {encoder!r}.")
    runtime = runtime or Runtime()
    src = os.path.abspath(src)             # the concat list resolves relative paths against /tmp
    expected = sum(end - start for start, end in segments)
    offset = start_time(src)               # cut points are on the file's own timeline
    escaped = src.replace("'", "'\\''")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix="highlightarr_", delete=False) as lst:
        for start, end in segments:
            lst.write(f"file '{escaped}'\n")
            if start + offset > 0:         # FLV refuses an explicit "inpoint 0"
                lst.write(f"inpoint {start + offset:.3f}\n")
            lst.write(f"outpoint {end + offset:.3f}\n")

    how, fallback = (encoder if reencode else "copy"), ""
    if how == ENCODER_HARDWARE:
        works, why = hardware_status(runtime.vaapi_device)
        if not works:
            log.warning("hardware encoder not usable (%s); using the software encoder", why)
            how, fallback = ENCODER_SOFTWARE, why

    def command(tracks: list[tuple[int, str]], how: str) -> list[str]:
        before, after = video_args(reencode, how, preset, crf, runtime.vaapi_device)
        maps = ["-map", "0:v:0", "-map", "0:a"]
        subtitle_codecs = []
        for number, (index, kind) in enumerate(tracks):
            maps += ["-map", f"0:{index}"]
            subtitle_codecs += [f"-c:s:{number}", kind]
        return gentle(["ffmpeg", "-nostdin", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
                       *_threads(runtime), *before, "-f", "concat", "-safe", "0", "-fflags", "+genpts",
                       "-i", lst.name, *maps, "-disposition:a", "0",
                       f"-disposition:a:{audio_track}", "default", *after, *_threads(runtime),
                       *subtitle_codecs, "-f", "matroska", dest], runtime)

    try:
        subtitles = subtitle_plan(src)
        tracks = subtitles
        label = f"{os.path.basename(src)}: cutting"
        while True:
            returncode, produced, text = _run_ffmpeg(command(tracks, how), expected, on_progress, cancel)
            without_subtitles = returncode != 0 and bool(tracks) and not produced
            in_software = returncode != 0 and not without_subtitles and how == ENCODER_HARDWARE
            # An attempt that is followed by a fallback is only a warning.
            will_retry = without_subtitles or in_software
            last = _log_ffmpeg_output(label, text, failed=returncode != 0 and not will_retry)
            if returncode == 0:
                return CutInfo(how, fallback)
            if without_subtitles:
                # If ffmpeg cannot take the subtitle tracks it says so before writing anything:
                # try again without them rather than failing the whole job.
                log.warning("%s: ffmpeg could not take the subtitle tracks; retrying without them",
                            os.path.basename(src))
                tracks = []
                continue
            if in_software:
                log.warning("%s: the hardware encode failed; retrying with the software encoder",
                            os.path.basename(src))
                how, fallback, tracks = ENCODER_SOFTWARE, "the hardware encode failed", subtitles
                continue
            raise ProcessingError(f"ffmpeg failed (exit code {returncode})" + (f": {last}" if last else ""))
    finally:
        os.unlink(lst.name)
