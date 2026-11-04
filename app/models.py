from datetime import UTC, datetime

from sqlmodel import Field, Relationship, SQLModel

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores no timezone)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Profile(SQLModel, table=True):
    """A reusable bundle of cutting settings — one per media type
    (Racing, Soccer, Movie, News, ...)."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)

    target_fraction: float = 0.25      # output length as a fraction of source
    event_start_sec: int = 0           # skip this much at the start (pre-show)
    end_trim_sec: int = 0              # ignore this much at the end (post-show)
    intro_sec: int = 150               # always keep this much right after event_start_sec
    outro_sec: int = 120               # always keep this much right before the (trimmed) end
    clip_before: int = 20              # seconds kept before a detected moment
    clip_after: int = 25               # seconds kept after a detected moment
    min_separation: int = 60           # minimum gap between two detected moments

    languages: str = "nld,eng"         # comma-separated preference order for audio detection
    delete_original: bool = False
    reencode: bool = False
    video_preset: str = "veryfast"     # x264 speed preset, used when re-encoding
    video_crf: int = 21                # x264 quality (lower = better/bigger), used when re-encoding
    video_encoder: str = "software"    # "software" (x264) or "hardware" (VAAPI GPU), when re-encoding

    workflows: list["Workflow"] = Relationship(back_populates="profile")


class Workflow(SQLModel, table=True):
    """Picks up files from a source folder, cuts them with a Profile and
    writes the result to a destination folder."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_path: str
    dest_path: str
    recursive: bool = True
    enabled: bool = True

    profile_id: int | None = Field(default=None, foreign_key="profile.id")
    profile: Profile | None = Relationship(back_populates="workflows")


class ActivityEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # Nullable + a name snapshot, so activity survives deleting the workflow.
    workflow_id: int | None = Field(default=None, foreign_key="workflow.id")
    workflow_name: str = ""
    source_path: str
    dest_path: str | None = None
    status: str  # STATUS_SUCCESS | STATUS_FAILED
    message: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Setting(SQLModel, table=True):
    """Key/value overrides edited from the Settings page. A missing row means
    "use the container default" (the POLL_SEC / SETTLE_SEC env vars)."""
    key: str = Field(primary_key=True)
    value: str
