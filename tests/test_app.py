from sqlmodel import Session, select

from app.db import engine, seed_defaults
from app.models import ActivityEntry, Profile, Workflow

PROFILE = {"name": "News", "target_fraction": "0.2", "event_start_sec": "0", "end_trim_sec": "0",
           "intro_sec": "60", "outro_sec": "60", "clip_before": "10", "clip_after": "10",
           "min_separation": "30", "languages": "eng"}


def _workflow(media, profile_id=1, **over):
    return {"name": "Race", "profile_id": str(profile_id), "source_path": f"{media}/in",
            "dest_path": f"{media}/out", "recursive": "on", "enabled": "on", **over}


def test_seed_defaults_is_idempotent():
    seed_defaults()
    seed_defaults()
    with Session(engine) as s:
        names = {p.name for p in s.exec(select(Profile))}
    assert names == {"Movies", "TV shows", "News", "Sport", "Racing", "Soccer"}


def test_pages_render(client):
    seed_defaults()
    for url in ("/activity", "/settings", "/profiles", "/profiles/new", "/workflows",
                "/workflows/new", "/healthz", "/api/status"):
        assert client.get(url).status_code == 200, url
    assert client.get("/api/status").json() == {"current": None}


def test_unknown_ids_are_404_not_500(client):
    assert client.get("/profiles/99/edit").status_code == 404
    assert client.get("/workflows/99/edit").status_code == 404
    assert client.post("/profiles/save", data={**PROFILE, "id": "99"}).status_code == 404
    assert client.post("/workflows/99/delete").status_code == 404


def test_profile_create_edit_validate(client):
    r = client.post("/profiles/save", data=PROFILE)
    assert r.status_code == 303
    with Session(engine) as s:
        p = s.exec(select(Profile)).one()
        assert p.name == "News" and p.target_fraction == 0.2 and p.reencode is False
    r = client.post("/profiles/save", data={**PROFILE, "id": "1", "name": "News 2", "reencode": "on"})
    assert r.status_code == 303
    with Session(engine) as s:
        assert s.get(Profile, 1).reencode is True and s.get(Profile, 1).name == "News 2"
    bad = client.post("/profiles/save", data={**PROFILE, "target_fraction": "7"})
    r = client.post("/profiles/save", data={**PROFILE, "id": "1", "reencode": "on",
                                            "video_preset": "ultrafast", "video_crf": "24"})
    assert r.status_code == 303
    with Session(engine) as s:
        p = s.get(Profile, 1)
        assert (p.video_preset, p.video_crf) == ("ultrafast", 24)
    form = client.get("/profiles/1/edit").text
    assert '<option value="ultrafast" selected>' in form and 'name="video_crf" value="24"' in form
    bad = client.post("/profiles/save", data={**PROFILE, "target_fraction": "7"})
    assert bad.status_code == 400 and "Target length must be at most 1" in bad.text
    assert 'value="7"' in bad.text                       # the form keeps what was typed


def test_workflow_lifecycle_and_activity_survives_delete(client, media):
    client.post("/profiles/save", data=PROFILE)
    assert client.post("/workflows/save", data=_workflow(media)).status_code == 303
    with Session(engine) as s:
        wf = s.exec(select(Workflow)).one()
        s.add(ActivityEntry(workflow_id=wf.id, workflow_name=wf.name, source_path="/x/a.mkv",
                           status="success"))
        s.commit()
    assert "Race" in client.get("/workflows").text
    assert "Race" in client.get("/activity").text

    assert client.post("/profiles/1/delete").headers["location"] == "/profiles?error=in_use"
    assert "used by a workflow" in client.get("/profiles?error=in_use").text

    assert client.post("/workflows/1/delete").status_code == 303
    with Session(engine) as s:
        assert s.exec(select(Workflow)).first() is None
        entry = s.exec(select(ActivityEntry)).one()
        assert entry.workflow_id is None and entry.workflow_name == "Race"
    assert client.post("/profiles/1/delete").headers["location"] == "/profiles"


def test_workflow_validation(client, media):
    client.post("/profiles/save", data=PROFILE)
    for over, message in (({"dest_path": "/etc"}, "inside a mounted media folder"),
                          ({"source_path": "relative"}, "absolute path"),
                          ({"profile_id": "42"}, "Profile does not exist")):
        r = client.post("/workflows/save", data=_workflow(media, **over))
        assert r.status_code == 400 and message in r.text, over
    with Session(engine) as s:
        assert s.exec(select(Workflow)).first() is None


def test_names_cannot_break_out_of_the_confirm_dialog(client, media):
    client.post("/profiles/save", data=PROFILE)
    client.post("/workflows/save", data=_workflow(media, name="x'); alert(1); ('"))
    html = client.get("/workflows").text
    assert "onsubmit" not in html                      # no inline JS built from the name
    assert "data-confirm=\"Delete workflow x&#39;); alert(1); (&#39;?" in html   # HTML-escaped data


def test_settings_roundtrip(client):
    bad = client.post("/settings/save", data={"poll_sec": "2", "settle_sec": "abc"})
    assert bad.status_code == 400
    assert "between 5 and 86400" in bad.text and "whole number" in bad.text
    assert client.post("/settings/save", data={"poll_sec": "30", "settle_sec": "45"}).status_code == 303
    page = client.get("/settings").text
    assert "<strong>30s</strong> (custom)" in page and "<strong>45s</strong> (custom)" in page
    client.post("/settings/save", data={"poll_sec": "", "settle_sec": ""})
    assert "(container default)" in client.get("/settings").text


def test_browse_api(client, media):
    data = client.get("/api/browse").json()
    assert {d["name"] for d in data["dirs"]} >= {"in", "out"}
    assert client.get("/api/browse", params={"path": "/etc"}).json()["parent"] is None


def test_cross_origin_posts_are_blocked(client):
    data = {"poll_sec": "30", "settle_sec": ""}
    evil = client.post("/settings/save", data=data, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    assert client.post("/settings/save", data=data, headers={"Origin": "http://null"}).status_code == 403
    same = client.post("/settings/save", data=data, headers={"Origin": "http://testserver"})
    assert same.status_code == 303
    assert client.post("/settings/save", data=data).status_code == 303          # no Origin: scripts, curl
    proxied = client.post("/settings/save", data=data, headers={
        "Origin": "https://highlights.home.lan", "X-Forwarded-Host": "highlights.home.lan"})
    assert proxied.status_code == 303
    reading = client.get("/activity", headers={"Origin": "http://evil.example"})
    assert reading.status_code == 200                                    # reads are fine


def test_activity_times_are_shown_in_the_configured_time_zone(client, monkeypatch):
    import time
    from datetime import datetime
    monkeypatch.setenv("TZ", "UTC-2")           # POSIX style: two hours ahead of UTC
    time.tzset()
    try:
        with Session(engine) as s:
            s.add(ActivityEntry(workflow_name="Race", source_path="/x/a.mkv", status="success",
                                message="ok", created_at=datetime(2026, 10, 24, 20, 41)))
            s.commit()
        page = client.get("/activity").text
        assert "2026-10-24 22:41" in page and "20:41" not in page and "(UTC)" not in page
    finally:
        monkeypatch.undo()
        time.tzset()


def test_activity_is_the_home_page_and_first_in_the_menu(client):
    import re
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307 and home.headers["location"] == "/activity"
    page = client.get("/", follow_redirects=True)                   # lands on Activity
    assert page.status_code == 200 and "<h1>Activity</h1>" in page.text
    assert re.findall(r'<nav class="sidebar">(.*?)</nav>', page.text, re.S)
    links = re.findall(r'<a href="(/[a-z]*)" class="[a-z]*">', page.text)
    assert links == ["/activity", "/profiles", "/workflows", "/logs", "/settings", "/about"]
    assert "Dashboard" not in page.text
    assert '<a href="/activity" class="active">' in page.text
    assert client.get("/dashboard").status_code == 404


def test_the_visible_name_is_capitalised(client):
    page = client.get("/activity").text
    assert "<title>Highlightarr · Activity</title>" in page
    assert page.count("> Highlightarr<") == 2                       # top bar (phone) and sidebar (desktop)
    assert "highlightarr<" not in page.replace("</title>", "<")
    assert "What Highlightarr did" in client.get("/logs").text
    assert client.get("/openapi.json").json()["info"]["title"] == "Highlightarr"
