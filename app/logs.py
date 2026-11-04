"""The app's log: written to a small rotating file next to the database (so it survives restarts)
and shown on the Logs page. Everything is also still printed to the container log."""
import logging
import logging.handlers
import os
import re
from dataclasses import dataclass, field

from . import config

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES, BACKUPS = 1_000_000, 3            # up to ~4 MB in total

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
# What the level filter on the page offers: name -> lowest level shown.
FILTERS = {"all": 0, "warning": 30, "error": 40}
LINE_CHOICES = (100, 300, 1000)

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3} (\w+)\s+(\S+?): (.*)$")
# Third-party loggers that do not propagate to the root logger: uvicorn's own start/stop messages.
_EXTRA_LOGGERS = ("uvicorn",)

_handler: logging.Handler | None = None
log = logging.getLogger("highlightarr.logs")


@dataclass
class Entry:
    date: str
    time: str
    level: str
    logger: str
    message: str            # may span several lines (tracebacks, ffmpeg output)


@dataclass
class LogView:
    entries: list[Entry] = field(default_factory=list)      # newest first
    matched: int = 0                                         # entries matching the filters
    total: int = 0                                           # entries in the files
    size: int = 0                                            # bytes on disk
    enabled: bool = True                                     # False when there is no log file


def setup(path: str | None = None) -> logging.Handler | None:
    """Start writing the log file. Safe to call again; returns None if file logging is off or
    the file cannot be written (the console log keeps working)."""
    global _handler
    path = config.LOG_PATH if path is None else path
    if _handler is not None or not path:
        return _handler
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                       encoding="utf-8")
    except OSError as exc:
        log.warning("cannot write the log file %s (%s); only the container log is kept", path, exc.strerror)
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.INFO)
    for name in ("", *_EXTRA_LOGGERS):
        logging.getLogger(name).addHandler(handler)
    _handler = handler
    return handler


def teardown() -> None:
    global _handler
    if _handler is None:
        return
    for name in ("", *_EXTRA_LOGGERS):
        logging.getLogger(name).removeHandler(_handler)
    _handler.close()
    _handler = None


def _files(path: str) -> list[str]:
    """The log file and its rotated backups, oldest first."""
    candidates = [f"{path}.{n}" for n in range(BACKUPS, 0, -1)] + [path]
    return [p for p in candidates if os.path.isfile(p)]


def _parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for line in text.splitlines():
        found = _LINE.match(line)
        if found:
            date, time_, level, name, message = found.groups()
            entries.append(Entry(date, time_, level.upper(), name, message))
        elif entries:                                   # a traceback or other continuation line
            entries[-1].message += "\n" + line
    return entries


def read(path: str | None = None, *, level: str = "all", query: str = "", limit: int = 300) -> LogView:
    path = config.LOG_PATH if path is None else path
    if not path:
        return LogView(enabled=False)
    files = _files(path)
    entries: list[Entry] = []
    size = 0
    for name in files:
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                entries += _parse(f.read())
            size += os.path.getsize(name)
        except OSError:
            continue
    lowest = FILTERS.get(level, 0)
    needle = query.strip().lower()
    wanted = [e for e in entries
              if LEVELS.get(e.level, 20) >= lowest
              and (not needle or needle in e.message.lower() or needle in e.logger.lower())]
    return LogView(entries=wanted[-limit:][::-1], matched=len(wanted), total=len(entries), size=size)


def raw(path: str | None = None) -> str:
    """The whole log as text, oldest first (for the download)."""
    path = config.LOG_PATH if path is None else path
    parts = []
    for name in _files(path) if path else []:
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                parts.append(f.read())
        except OSError:
            continue
    return "".join(parts)
