from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlmodel import col, select

from .. import worker
from ..deps import SessionDep
from ..models import ActivityEntry
from ..templating import templates

router = APIRouter()


@router.get("/")
def home():
    """Activity is the home page: what is being processed now, and what happened lately."""
    return RedirectResponse("/activity", status_code=307)


@router.get("/activity")
def activity_page(request: Request, session: SessionDep):
    activity = session.exec(select(ActivityEntry)
                            .order_by(col(ActivityEntry.created_at).desc()).limit(200)).all()
    return templates.TemplateResponse(request, "activity.html", {
        "activity": activity,
        "current": worker.get_current(),
    })


@router.get("/api/status")
def api_status():
    return {"current": worker.get_current()}


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
