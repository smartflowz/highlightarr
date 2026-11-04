from fastapi import APIRouter, Request

from .. import about
from ..templating import templates

router = APIRouter()


@router.get("/about")
def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", {"sections": about.collect()})
