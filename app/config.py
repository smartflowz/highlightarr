"""Environment-driven configuration, read once at import time."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DB_PATH = os.getenv("DB_PATH", "/config/highlightarr.db")

# The log file shown on the Logs page: next to the database unless LOG_PATH says otherwise.
# Set LOG_PATH to an empty value to log to the console only.
LOG_PATH = (os.getenv("LOG_PATH") if "LOG_PATH" in os.environ
            else os.path.join(os.path.dirname(DB_PATH) or ".", "highlightarr.log"))

# Folders workflows may read from / write to, and that the Browse button
# lists. Comma-separated container paths.
MEDIA_ROOTS = [r.strip() for r in os.getenv("MEDIA_ROOTS", "/media").split(",") if r.strip()]

# Defaults for the two settings editable on the Settings page.
POLL_SEC = _env_int("POLL_SEC", 60)
SETTLE_SEC = _env_int("SETTLE_SEC", 300)

# Being gentle on a small machine (also editable on the Settings page).
FFMPEG_NICE = _env_int("FFMPEG_NICE", 10)         # 0 = normal priority, 19 = lowest
FFMPEG_THREADS = _env_int("FFMPEG_THREADS", 0)    # 0 = let ffmpeg use every core
PROCESS_FROM = _env_int("PROCESS_FROM", -1)       # start processing at this hour (0-23); -1 = any time
PROCESS_UNTIL = _env_int("PROCESS_UNTIL", -1)     # ... and stop starting new files at this hour

# GPU used for hardware (VAAPI) encoding.
VAAPI_DEVICE = os.getenv("VAAPI_DEVICE", "/dev/dri/renderD128")

# Recordings that are picked up. The highlights file is always written as Matroska.
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".mts", ".webm",
                    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp")
OUTPUT_EXTENSION = ".mkv"
OUTPUT_SUFFIX = " - Highlights"     # "Race.mkv" -> "Race - Highlights.mkv"
ACTIVITY_KEEP = 1000                 # older activity rows are pruned
