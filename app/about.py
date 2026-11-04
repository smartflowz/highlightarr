"""Versions and runtime facts for the About page."""
import functools
import importlib.metadata as metadata
import os
import platform
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import config, media, settings
from .media import has_encoder
from .sources import human_duration
from .version import COMMIT, __version__

STARTED_AT = time.time()

# (label, distribution name)
PACKAGES = (("FastAPI", "fastapi"), ("Starlette", "starlette"), ("Uvicorn", "uvicorn"),
            ("Pydantic", "pydantic"), ("SQLModel", "sqlmodel"), ("SQLAlchemy", "sqlalchemy"),
            ("Jinja2", "jinja2"), ("NumPy", "numpy"))


@dataclass(frozen=True)
class Row:
    label: str
    value: str
    ok: bool = True        # False: shown as a problem (e.g. a missing tool)


@dataclass(frozen=True)
class Section:
    title: str
    rows: list[Row] = field(default_factory=list)


def package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "not installed"


def parse_tool_version(output: str) -> str | None:
    """'ffmpeg version 6.1.1-3ubuntu5 Copyright …' -> '6.1.1-3ubuntu5'."""
    first_line = output.splitlines()[0] if output.strip() else ""
    found = re.search(r"\bversion\s+(\S+)", first_line)
    return found.group(1) if found else None


def _run(command: list[str]) -> str | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None


@functools.cache
def tool_version(binary: str) -> str | None:
    out = _run([binary, "-version"])
    if not out:
        return None
    return parse_tool_version(out) or out.splitlines()[0][:60]


def os_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def container_name() -> str:
    if os.path.exists("/.dockerenv"):
        return "Docker"
    if os.path.exists("/run/.containerenv"):
        return "Podman"
    return "not detected"


def timezone_text() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return (f"{os.getenv('TZ') or 'not set (UTC)'} · {now.strftime('%Z')} "
            f"(UTC{offset[:3]}:{offset[3:]})")


def _file_size(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "not created yet"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def collect(now: float | None = None) -> list[Section]:
    now = time.time() if now is None else now
    started = datetime.fromtimestamp(STARTED_AT).astimezone().strftime("%Y-%m-%d %H:%M")
    app_rows = [Row("Version", __version__)]
    if COMMIT:
        app_rows.append(Row("Build", COMMIT))
    app_rows += [Row("Started", started), Row("Uptime", human_duration(now - STARTED_AT))]

    components = [Row("Python", f"{platform.python_version()} ({platform.python_implementation()})")]
    components += [Row(label, package_version(dist)) for label, dist in PACKAGES]
    components.append(Row("SQLite", sqlite3.sqlite_version))
    for binary in ("ffmpeg", "ffprobe"):
        version = tool_version(binary)
        components.append(Row(binary, version or "not found", ok=version is not None))
    encoder = has_encoder("libx264")
    components.append(Row("x264 encoder (re-encoding)", "available" if encoder else "missing", ok=encoder))
    works, why = media.hardware_status(config.VAAPI_DEVICE)
    components.append(Row("Hardware encoder (VAAPI)",
                          f"available ({config.VAAPI_DEVICE})" if works else f"not available: {why}"))

    system = [Row("Operating system", os_name()), Row("Kernel", platform.release()),
              Row("Architecture", platform.machine()), Row("Container", container_name())]
    if hasattr(os, "getuid"):
        system.append(Row("Runs as user", f"{os.getuid()}:{os.getgid()}"))
    system.append(Row("Time zone", timezone_text()))

    nice = settings.nice()
    processing = [Row("Priority (nice level)", f"{nice} (normal)" if nice == 0 else str(nice)),
                  Row("ffmpeg threads", str(settings.threads()) if settings.threads() else "automatic"),
                  Row("Processing window", settings.window_text())]

    storage = [Row("Database", f"{config.DB_PATH} ({_file_size(config.DB_PATH)})"),
               Row("Log file", f"{config.LOG_PATH} ({_file_size(config.LOG_PATH)})" if config.LOG_PATH
                   else "off (console only)"),
               Row("Media folders", ", ".join(config.MEDIA_ROOTS))]
    return [Section("Highlightarr", app_rows), Section("Components", components),
            Section("System", system), Section("Processing", processing), Section("Storage", storage)]
