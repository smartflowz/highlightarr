import logging

import pytest

from app import config, logs


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


SAMPLE = [
    "2026-10-26 07:00:01,123 INFO    highlightarr.worker: worker started: poll=60s settle=300s",
    "2026-10-26 07:05:00,001 INFO    highlightarr.worker: processing /media/a.mkv (workflow=races)",
    "2026-10-26 07:05:09,500 WARNING highlightarr.media: a.mkv: cutting: ffmpeg said (last 1 of 1 lines):",
    "Non-monotonous DTS in output stream",
    "2026-10-26 07:06:00,000 ERROR   highlightarr.worker: failed /media/b.mkv",
    "Traceback (most recent call last):",
    '  File "x.py", line 1, in <module>',
    "ValueError: boom",
    "2026-10-26 07:07:00,000 INFO    uvicorn.error: Shutting down",
]


@pytest.fixture
def logfile(tmp_path):
    path = tmp_path / "highlightarr.log"
    _write(path, SAMPLE)
    return str(path)


def test_parse_groups_continuation_lines_with_their_entry(logfile):
    view = logs.read(logfile, limit=100)
    assert view.total == 5 and view.matched == 5 and view.enabled
    assert [e.level for e in view.entries] == ["INFO", "ERROR", "WARNING", "INFO", "INFO"]   # newest first
    error = view.entries[1]
    assert error.logger == "highlightarr.worker" and (error.date, error.time) == ("2026-10-26", "07:06:00")
    assert error.message.splitlines() == ["failed /media/b.mkv", "Traceback (most recent call last):",
                                          '  File "x.py", line 1, in <module>', "ValueError: boom"]
    assert "Non-monotonous DTS" in view.entries[2].message


def test_filters_and_limit(logfile):
    assert [e.level for e in logs.read(logfile, level="warning").entries] == ["ERROR", "WARNING"]
    assert [e.level for e in logs.read(logfile, level="error").entries] == ["ERROR"]
    assert logs.read(logfile, level="nonsense").matched == 5                     # unknown filter: show all
    by_text = logs.read(logfile, query="  B.MKV ")                               # case-insensitive, trimmed
    assert [e.message.splitlines()[0] for e in by_text.entries] == ["failed /media/b.mkv"]
    assert [e.logger for e in logs.read(logfile, query="uvicorn").entries] == ["uvicorn.error"]
    newest = logs.read(logfile, limit=2)
    assert len(newest.entries) == 2 and newest.matched == 5                      # newest two, count of all
    assert newest.entries[0].message == "Shutting down"


def test_rotated_files_are_read_oldest_first(tmp_path):
    path = tmp_path / "app.log"
    _write(tmp_path / "app.log.2", ["2026-10-24 01:00:00,000 INFO    highlightarr.x: oldest"])
    _write(tmp_path / "app.log.1", ["2026-10-25 01:00:00,000 INFO    highlightarr.x: older"])
    _write(path, ["2026-10-26 01:00:00,000 INFO    highlightarr.x: newest"])
    view = logs.read(str(path))
    assert [e.message for e in view.entries] == ["newest", "older", "oldest"]
    assert "oldest" in logs.raw(str(path)).splitlines()[0]                        # download: chronological
    assert logs.raw(str(path)).splitlines()[-1].endswith("newest")


def test_missing_empty_and_disabled(tmp_path):
    assert logs.read(str(tmp_path / "none.log")).total == 0
    assert logs.read("").enabled is False and logs.raw("") == ""
    (tmp_path / "blank.log").write_text("")
    assert logs.read(str(tmp_path / "blank.log")).entries == []


def test_setup_writes_the_file_and_teardown_stops(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = str(tmp_path / "sub" / "highlightarr.log")
    handler = logs.setup(path)
    try:
        assert handler is not None and logs.setup(path) is handler              # idempotent
        logging.getLogger("highlightarr.test").warning("disk almost full")
        logging.getLogger("highlightarr.test").info("all good again")
        view = logs.read(path, level="warning")
        assert [e.message for e in view.entries] == ["disk almost full"]
        assert view.entries[0].logger == "highlightarr.test"
    finally:
        logs.teardown()
    logging.getLogger("highlightarr.test").warning("after teardown")
    assert "after teardown" not in logs.raw(path)
    logs.teardown()                                                              # harmless twice


def test_unwritable_location_does_not_break_startup(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert logs.setup(str(blocker / "highlightarr.log")) is None                 # a folder cannot be made
    assert logs.setup("") is None


def test_logs_page(client):
    logging.getLogger("highlightarr.test").warning("something <b>odd</b> happened")
    logging.getLogger("highlightarr.test").info("routine note")
    page = client.get("/logs")
    assert page.status_code == 200
    assert "something &lt;b&gt;odd&lt;/b&gt; happened" in page.text      # escaped, never raw HTML
    assert "<b>odd</b>" not in page.text
    assert "routine note" in page.text and "Download" in page.text
    warnings = client.get("/logs?level=warning").text
    assert "something &lt;b&gt;odd" in warnings and "routine note" not in warnings
    searched = client.get("/logs?q=ODD").text
    assert "something" in searched and "routine note" not in searched
    assert client.get("/logs?level=bogus&lines=7").status_code == 200            # bad input falls back
    assert '<a href="/logs" class="">' in client.get("/activity").text           # its own menu entry
    assert '<a href="/logs" class="active">' in page.text
    assert '<a href="/about" class="active">' not in page.text                  # not part of About


def test_logs_download(client):
    logging.getLogger("highlightarr.test").warning("for the download")
    response = client.get("/logs/download")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain")
    assert 'filename="highlightarr.log"' in response.headers["content-disposition"]
    assert "for the download" in response.text


def test_logs_page_when_file_logging_is_off(client, monkeypatch):
    monkeypatch.setattr(config, "LOG_PATH", "")
    page = client.get("/logs")
    assert page.status_code == 200 and "switched off" in page.text and "Download" not in page.text
