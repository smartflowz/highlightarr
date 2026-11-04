from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from .. import config, db, forms, media
from ..deps import FormDep, SessionDep
from ..models import Profile
from ..templating import templates

router = APIRouter(prefix="/profiles")


def _form_page(request: Request, values: dict, profile_id: int | None = None,
               errors: list[str] | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, "profile_form.html", {
        "values": values, "profile_id": profile_id, "errors": errors or [],
        "hardware": media.hardware_status(config.VAAPI_DEVICE),
    }, status_code=status_code)


def _get_or_404(session, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


@router.get("")
def list_profiles(request: Request, session: SessionDep, error: str = "", added: int = -1):
    profiles = session.exec(select(Profile).order_by(Profile.name)).all()
    return templates.TemplateResponse(request, "profiles.html", {
        "profiles": profiles, "error": error, "added": added,
        "missing_defaults": db.missing_default_names(session),
    })


@router.post("/defaults")
def add_defaults(session: SessionDep):
    """Add the ready-made profiles that are missing (existing profiles are left alone)."""
    return RedirectResponse(f"/profiles?added={db.add_default_profiles(session)}", status_code=303)


@router.get("/new")
def new_profile_form(request: Request):
    return _form_page(request, forms.PROFILE_DEFAULTS)


@router.get("/{profile_id}/edit")
def edit_profile_form(request: Request, profile_id: int, session: SessionDep):
    profile = _get_or_404(session, profile_id)
    return _form_page(request, profile.model_dump(), profile_id)


@router.post("/save")
def save_profile(request: Request, form: FormDep, session: SessionDep):
    profile_id = int(form["id"]) if form.get("id", "").isdigit() else None
    profile = _get_or_404(session, profile_id) if profile_id else Profile()
    data, errors = forms.validate(forms.ProfileForm, form)
    if errors:
        return _form_page(request, form, profile_id, errors, status_code=400)
    for key, value in data.model_dump().items():
        setattr(profile, key, value)
    session.add(profile)
    session.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/{profile_id}/delete")
def delete_profile(profile_id: int, session: SessionDep):
    profile = _get_or_404(session, profile_id)
    if profile.workflows:
        return RedirectResponse("/profiles?error=in_use", status_code=303)
    session.delete(profile)
    session.commit()
    return RedirectResponse("/profiles", status_code=303)
