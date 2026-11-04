"""The ready-made profiles must validate and give sensible results for realistic recordings."""
import numpy as np
import pytest
from sqlmodel import Session, select

from app import db, forms
from app.db import DEFAULT_PROFILES, engine, seed_defaults  # the real one: conftest stubs db.seed_defaults
from app.detector import CutConfig, choose_segments
from app.models import Profile

NAMES = ["Movies", "TV shows", "News", "Sport", "Racing", "Soccer"]

# Shortest and longest recordings each profile is meant for (seconds).
REALISTIC = {
    "Movies": (75 * 60, 170 * 60), "TV shows": (20 * 60, 70 * 60), "News": (10 * 60, 50 * 60),
    "Sport": (45 * 60, 130 * 60), "Racing": (60 * 60, 150 * 60), "Soccer": (60 * 60, 130 * 60),
}


def test_there_is_a_default_profile_for_each_kind_of_content():
    assert [d["name"] for d in DEFAULT_PROFILES] == NAMES


@pytest.mark.parametrize("settings", DEFAULT_PROFILES, ids=NAMES)
def test_defaults_pass_the_same_validation_as_the_profile_form(settings):
    form = forms.ProfileForm.model_validate({**forms.PROFILE_DEFAULTS, **settings})
    assert form.name == settings["name"]
    assert all(getattr(form, key) == value for key, value in settings.items())


@pytest.mark.parametrize("settings", DEFAULT_PROFILES, ids=NAMES)
def test_defaults_give_a_result_near_the_target_for_realistic_lengths(settings):
    """process() rejects a result outside 0.5x-1.5x of the target: no default may run into that."""
    cfg = CutConfig.from_profile(Profile(**settings))
    shortest, longest = REALISTIC[settings["name"]]
    rng = np.random.default_rng(7)
    for total in (shortest, (shortest + longest) // 2, longest):
        rms = rng.gamma(2.0, 400.0, total)                      # speech-like loudness, one value per second
        segments = choose_segments(rms, float(total), (total - cfg.event_start_sec - cfg.end_trim_sec)
                                   * cfg.target_fraction, cfg)
        kept = sum(end - start for start, end in segments)
        target = (total - cfg.event_start_sec - cfg.end_trim_sec) * cfg.target_fraction
        assert 0.5 * target <= kept <= 1.5 * target, (settings["name"], total, kept, target)
        assert segments[0][0] >= cfg.event_start_sec and segments[-1][1] <= total - cfg.end_trim_sec


def test_the_credits_of_a_film_are_never_part_of_the_highlights():
    movies = next(d for d in DEFAULT_PROFILES if d["name"] == "Movies")
    cfg = CutConfig.from_profile(Profile(**movies))
    total = 100 * 60
    rms = np.random.default_rng(1).gamma(2.0, 400.0, total)
    rms[-300:] *= 5                                              # loud credits music
    assert choose_segments(rms, float(total), total * 0.12, cfg)[-1][1] <= total - movies["end_trim_sec"]


def test_first_start_creates_every_default_profile():
    with Session(engine) as session:
        for profile in session.exec(select(Profile)).all():
            session.delete(profile)
        session.commit()
    seed_defaults()
    seed_defaults()                                              # a second start adds nothing
    with Session(engine) as session:
        assert sorted(p.name for p in session.exec(select(Profile)).all()) == sorted(NAMES)


def test_add_defaults_only_adds_what_is_missing_and_keeps_existing_profiles(client):
    with Session(engine) as session:
        for profile in session.exec(select(Profile)).all():
            session.delete(profile)
        session.add(Profile(name="Movies", target_fraction=0.5))          # the user's own version
        session.add(Profile(name="My own"))
        session.commit()
        assert set(db.missing_default_names(session)) == set(NAMES) - {"Movies"}

    page = client.get("/profiles").text
    assert "Add default profiles (5)" in page and "Missing: TV shows" in page

    response = client.post("/profiles/defaults")
    assert response.status_code == 303 and response.headers["location"] == "/profiles?added=5"
    page = client.get("/profiles?added=5").text
    assert "Added 5 default profiles." in page and "Add default profiles" not in page
    with Session(engine) as session:
        profiles = {p.name: p for p in session.exec(select(Profile)).all()}
        assert set(profiles) == set(NAMES) | {"My own"}
        assert profiles["Movies"].target_fraction == 0.5                  # untouched
    again = client.post("/profiles/defaults")
    assert again.headers["location"] == "/profiles?added=0"
    assert "Added" not in client.get("/profiles?added=0").text
