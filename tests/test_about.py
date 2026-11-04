import subprocess

import pytest

from app import about
from app.version import __version__


@pytest.fixture(autouse=True)
def fresh_tool_cache():
    about.tool_version.cache_clear()
    about.has_encoder.cache_clear()
    yield
    about.tool_version.cache_clear()
    about.has_encoder.cache_clear()


def _rows():
    return {row.label: row for section in about.collect() for row in section.rows}


def test_parse_tool_version():
    line = "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023"
    assert about.parse_tool_version(line) == "6.1.1-3ubuntu5"
    assert about.parse_tool_version("ffprobe version N-113456-gabcdef Copyright") == "N-113456-gabcdef"
    assert about.parse_tool_version("") is None
    assert about.parse_tool_version("something else entirely") is None


def test_collect_lists_every_version_that_matters():
    rows = _rows()
    assert rows["Version"].value == __version__
    for label in ("Python", "FastAPI", "Starlette", "Uvicorn", "Pydantic", "SQLModel", "SQLAlchemy",
                  "Jinja2", "NumPy", "SQLite", "ffmpeg", "ffprobe", "Operating system", "Architecture",
                  "Time zone", "Database", "Media folders", "Uptime", "Started"):
        assert rows[label].value, label
    assert all(rows[name].value != "not installed" for name in ("FastAPI", "SQLModel", "NumPy"))
    assert rows["ffmpeg"].ok and rows["x264 encoder (re-encoding)"].ok      # the test machine has both


def test_missing_tools_are_reported_not_fatal(monkeypatch):
    def nothing(*args, **kwargs):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(subprocess, "run", nothing)
    rows = _rows()
    assert rows["ffmpeg"].value == "not found" and not rows["ffmpeg"].ok
    assert rows["ffprobe"].value == "not found" and not rows["ffprobe"].ok
    assert rows["x264 encoder (re-encoding)"].value == "missing" and not rows["x264 encoder (re-encoding)"].ok


def test_build_commit_only_shows_when_known(monkeypatch):
    assert "Build" not in _rows() or about.COMMIT
    monkeypatch.setattr(about, "COMMIT", "abc1234")
    assert _rows()["Build"].value == "abc1234"


def test_uptime_is_measured_from_the_start():
    sections = about.collect(now=about.STARTED_AT + 3700)
    uptime = next(row for section in sections for row in section.rows if row.label == "Uptime")
    assert uptime.value == "1 h 1 min"


def test_timezone_text_follows_tz(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "UTC-2")
    time.tzset()
    try:
        assert about.timezone_text().endswith("(UTC+02:00)") and about.timezone_text().startswith("UTC-2")
    finally:
        monkeypatch.undo()
        time.tzset()


def test_about_page(client, monkeypatch):
    page = client.get("/about")
    assert page.status_code == 200
    for text in (__version__, "FastAPI", "ffmpeg", "SQLite", "Components", "System", "Storage"):
        assert text in page.text, text
    assert 'href="/about"' in client.get("/activity").text                      # in the menu

    def nothing(*args, **kwargs):
        raise FileNotFoundError("no such file")
    about.tool_version.cache_clear()
    about.has_encoder.cache_clear()
    monkeypatch.setattr(subprocess, "run", nothing)
    broken = client.get("/about")
    assert broken.status_code == 200 and "not found" in broken.text and "missing" in broken.text
