import os

from app import forms, paths


def test_is_within_handles_prefix_siblings(media):
    assert paths.is_within(f"{media}/in", media)
    assert paths.is_within(media, media)
    assert not paths.is_within(f"{media}-evil/x", media)
    assert not paths.is_within("/etc", media)


def test_browse_lists_visible_folders_only(media):
    os.makedirs(f"{media}/.hidden", exist_ok=True)
    os.makedirs(f"{media}/@eaDir", exist_ok=True)
    result = paths.browse()
    names = [d["name"] for d in result["dirs"]]
    assert "in" in names and "out" in names
    assert ".hidden" not in names and "@eaDir" not in names
    assert result["parent"] is None


def test_browse_cannot_leave_media_roots(media):
    for attempt in ("/etc", f"{media}/../..", f"{media}-evil"):
        assert paths.browse(attempt)["path"] == os.path.realpath(media)


def test_browse_walks_up_from_a_path_that_does_not_exist_yet(media):
    result = paths.browse(f"{media}/out/new/deeper")
    assert result["path"] == os.path.realpath(f"{media}/out")
    assert result["parent"] == os.path.realpath(media)


def test_profile_form_errors_are_readable():
    _, errors = forms.validate(forms.ProfileForm, {
        "name": "", "target_fraction": "1.5", "event_start_sec": "-1", "end_trim_sec": "x",
        "intro_sec": "1", "outro_sec": "1", "clip_before": "1", "clip_after": "1",
        "min_separation": "1"})
    text = " | ".join(errors)
    assert "Name is required" in text
    assert "Target length must be at most 1" in text
    assert "Skip at start must be at least 0" in text
    assert "Trim at end must be a whole number" in text


def test_profile_form_normalises_languages_and_checkboxes():
    data, errors = forms.validate(forms.ProfileForm, {
        **{k: str(v) for k, v in forms.PROFILE_DEFAULTS.items() if k not in ("delete_original", "reencode")},
        "name": " Racing ", "languages": " NLD , eng,, ", "reencode": "on"})
    assert not errors
    assert data.name == "Racing" and data.languages == "nld,eng"
    assert data.reencode is True and data.delete_original is False
    assert (data.video_preset, data.video_crf) == ("veryfast", 21)


def test_encoder_settings_are_validated():
    base = {k: str(v) for k, v in forms.PROFILE_DEFAULTS.items() if k not in ("delete_original", "reencode")}
    ok, errors = forms.validate(forms.ProfileForm,
                                {**base, "name": "x", "video_preset": "fast", "video_crf": "18"})
    assert not errors and (ok.video_preset, ok.video_crf) == ("fast", 18)
    _, errors = forms.validate(forms.ProfileForm, {**base, "name": "x", "video_preset": "placebo"})
    assert errors == ["Encoding speed is not one of the available choices"]
    _, errors = forms.validate(forms.ProfileForm, {**base, "name": "x", "video_crf": "40"})
    assert errors == ["Video quality must be at most 30"]
    _, errors = forms.validate(forms.ProfileForm, {**base, "name": "x", "video_crf": "5"})
    assert errors == ["Video quality must be at least 15"]


def _wf(media, **over):
    base = {"name": "w", "profile_id": "1", "source_path": f"{media}/in",
            "dest_path": f"{media}/out", "recursive": "on", "enabled": "on"}
    merged = {**base, **over}
    # like a browser: an unticked checkbox is simply absent from the form
    return forms.validate(forms.WorkflowForm, {k: v for k, v in merged.items() if v is not None})


def test_workflow_form_accepts_valid_and_normalises(media):
    data, errors = _wf(media, source_path=f"{media}/in/", dest_path=f"{media}/out//")
    assert not errors
    assert data.source_path == f"{media}/in" and data.dest_path == f"{media}/out"


def test_workflow_form_path_rules(media):
    assert "must be an absolute path" in _wf(media, source_path="in")[1][0]
    assert "inside a mounted media folder" in _wf(media, dest_path="/etc")[1][0]


def test_destination_may_be_the_source_folder_or_inside_it(media):
    """Highlights files end in " - Highlights" and are never picked up again, so these are safe."""
    for dest in (f"{media}/in", f"{media}/in/sub", f"{media}"):
        for recursive in ("on", None):
            data, errors = _wf(media, dest_path=dest, recursive=recursive)
            assert not errors and data.dest_path == dest, (dest, recursive)


def test_browse_hides_symlinks_that_lead_outside_the_media_roots(media, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, f"{media}/link-outside")
    os.symlink(f"{media}/out", f"{media}/link-inside")
    try:
        names = [d["name"] for d in paths.browse()["dirs"]]
        assert "link-outside" not in names
        assert "link-inside" in names
    finally:
        os.unlink(f"{media}/link-outside")
        os.unlink(f"{media}/link-inside")
