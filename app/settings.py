"""Runtime settings. Env vars are the defaults; values saved on the Settings
page override them and apply without a restart (the worker re-reads them
every second)."""
import contextlib
from dataclasses import dataclass

from sqlmodel import Session, select

from . import config
from .db import engine
from .models import Setting


@dataclass(frozen=True)
class Spec:
    label: str
    default: int
    minimum: int
    maximum: int
    help: str
    kind: str = "seconds"        # "seconds", "hour" or "number"
    group: str = "Scanning"
    special: str = ""            # shown instead of the number for `special_value`
    special_value: int | None = None

    @property
    def suffix(self) -> str:
        return {"seconds": " (seconds)", "hour": " (hour, 0–23)"}.get(self.kind, "")

    def format(self, value: int) -> str:
        if self.special and value == self.special_value:
            return self.special
        if self.kind == "seconds":
            return f"{value}s"
        if self.kind == "hour":
            return f"{value:02d}:00"
        return str(value)


SPEC: dict[str, Spec] = {
    "poll_sec": Spec("Poll interval", config.POLL_SEC, 5, 86400,
                     "How often the source folders are scanned for new files."),
    "settle_sec": Spec("Settle time", config.SETTLE_SEC, 0, 86400,
                       "How long a file's size must stay unchanged before it is processed, "
                       "so files still being copied are left alone."),
    "ffmpeg_nice": Spec("Processing priority (nice level)", config.FFMPEG_NICE, 0, 19,
                        "How low a priority ffmpeg runs at, so everything else on the machine stays "
                        "responsive. 0 is normal, 19 is the lowest. Disk access is lowered too.",
                        kind="number", group="Processing"),
    "ffmpeg_threads": Spec("ffmpeg threads", config.FFMPEG_THREADS, 0, 64,
                           "How many CPU threads ffmpeg may use. Automatic uses every core; 1 leaves the "
                           "rest of the CPU free but makes encoding take longer.",
                           kind="number", group="Processing", special="automatic", special_value=0),
    "window_start": Spec("Process only from", config.PROCESS_FROM, 0, 23,
                         "Together with the next setting this limits processing to a time of day, "
                         "for example 22 to 7 for overnight (local time, see TZ). A file that is already "
                         "running is finished. Leave both blank to process at any time.",
                         kind="hour", group="Processing", special="any time", special_value=-1),
    "window_end": Spec("Process only until", config.PROCESS_UNTIL, 0, 23,
                       "The hour at which no new file is started any more.",
                       kind="hour", group="Processing", special="any time", special_value=-1),
}

_cache: dict[str, int] | None = None


def _overrides() -> dict[str, int]:
    global _cache
    if _cache is None:
        with Session(engine) as session:
            rows = session.exec(select(Setting)).all()
        loaded = {}
        for row in rows:
            if row.key in SPEC:
                with contextlib.suppress(ValueError):
                    loaded[row.key] = int(row.value)
        _cache = loaded
    return _cache


def get(key: str) -> int:
    return _overrides().get(key, SPEC[key].default)


def is_overridden(key: str) -> bool:
    return key in _overrides()


def poll_sec() -> int:
    return get("poll_sec")


def settle_sec() -> int:
    return get("settle_sec")


def nice() -> int:
    return get("ffmpeg_nice")


def threads() -> int:
    return get("ffmpeg_threads")


def window() -> tuple[int, int] | None:
    """The (start hour, end hour) processing is limited to, or None for any time."""
    start, end = get("window_start"), get("window_end")
    return None if start < 0 or end < 0 or start == end else (start, end)


def in_window(hour: int) -> bool:
    limits = window()
    if limits is None:
        return True
    start, end = limits
    return start <= hour < end if start < end else hour >= start or hour < end


def window_text() -> str:
    limits = window()
    return f"{limits[0]:02d}:00–{limits[1]:02d}:00" if limits else "any time"


def parse_form(raw: dict[str, str]) -> tuple[dict[str, int | None], list[str]]:
    """Validate submitted values. Blank means "use the container default"
    (None). Returns (values, error messages)."""
    values: dict[str, int | None] = {}
    errors: list[str] = []
    for key, spec in SPEC.items():
        text = (raw.get(key) or "").strip()
        if not text:
            values[key] = None
            continue
        try:
            number = int(text)
        except ValueError:
            errors.append(f"{spec.label} must be a whole number.")
            continue
        if not spec.minimum <= number <= spec.maximum:
            errors.append(f"{spec.label} must be between {spec.minimum} and {spec.maximum}.")
            continue
        values[key] = number
    # Both hours or neither (checked only when both were valid, so an invalid hour is not reported twice).
    if "window_start" in values and "window_end" in values \
            and (values["window_start"] is None) != (values["window_end"] is None):
        errors.append("Set both the start and the end hour of the processing window, or leave both blank.")
    return values, errors


def save(values: dict[str, int | None]) -> None:
    """None removes the override (falls back to the env default)."""
    global _cache
    with Session(engine) as session:
        for key, value in values.items():
            row = session.get(Setting, key)
            if value is None:
                if row:
                    session.delete(row)
            elif row:
                row.value = str(value)
                session.add(row)
            else:
                session.add(Setting(key=key, value=str(value)))
        session.commit()
    _cache = None  # reload on next read
