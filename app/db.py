import os

from sqlmodel import Session, SQLModel, create_engine, select

from . import config

# timeout: wait for a lock instead of failing — the worker and the web UI
# share this file.
engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


def init_db() -> None:
    from . import models  # noqa: F401  (registers the tables)
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    SQLModel.metadata.create_all(engine)


# Ready-made profiles. The detector looks for moments where the speech stands out from the
# surrounding minutes: that suits commentary best (sport). In films and series it finds the
# intense scenes, and in the news mostly the opening headlines plus a spread of items. Intro and
# outro stay short enough that even a short recording lands near the target length.
DEFAULT_PROFILES = [
    {"name": "Movies", "target_fraction": 0.12, "end_trim_sec": 420, "intro_sec": 60, "outro_sec": 60,
     "clip_before": 15, "clip_after": 20, "min_separation": 120, "reencode": True},
    {"name": "TV shows", "target_fraction": 0.25, "end_trim_sec": 90, "intro_sec": 45, "outro_sec": 45,
     "clip_before": 15, "clip_after": 20, "min_separation": 90, "reencode": True},
    {"name": "News", "target_fraction": 0.25, "intro_sec": 90, "outro_sec": 30,
     "clip_before": 10, "clip_after": 20, "min_separation": 120, "reencode": True},
    {"name": "Sport", "target_fraction": 0.20, "intro_sec": 90, "outro_sec": 90,
     "clip_before": 25, "clip_after": 25, "min_separation": 75, "reencode": True},
    {"name": "Racing", "target_fraction": 0.25, "intro_sec": 150, "outro_sec": 120,
     "clip_before": 20, "clip_after": 25, "min_separation": 60},
    {"name": "Soccer", "target_fraction": 0.15, "intro_sec": 60, "outro_sec": 60,
     "clip_before": 30, "clip_after": 20, "min_separation": 90},
]


def missing_default_names(session: Session) -> list[str]:
    from .models import Profile
    have = {p.name for p in session.exec(select(Profile)).all()}
    return [d["name"] for d in DEFAULT_PROFILES if d["name"] not in have]


def add_default_profiles(session: Session) -> int:
    """Add the default profiles that do not exist yet (by name); never changes existing ones.
    Returns how many were added."""
    from .models import Profile
    missing = set(missing_default_names(session))
    for settings_ in DEFAULT_PROFILES:
        if settings_["name"] in missing:
            session.add(Profile(**settings_))
    session.commit()
    return len(missing)


def seed_defaults() -> None:
    """First start: an empty database gets all the default profiles."""
    from .models import Profile
    with Session(engine) as session:
        if not session.exec(select(Profile)).first():
            add_default_profiles(session)


def get_session():
    with Session(engine) as session:
        yield session
