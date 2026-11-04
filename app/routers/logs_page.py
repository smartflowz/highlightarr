from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import logs
from ..templating import templates

router = APIRouter(prefix="/logs")


@router.get("")
def logs_view(request: Request, level: str = "all", q: str = "", lines: int = 300):
    lines = lines if lines in logs.LINE_CHOICES else 300
    level = level if level in logs.FILTERS else "all"
    return templates.TemplateResponse(request, "logs.html", {
        "view": logs.read(level=level, query=q, limit=lines),
        "level": level, "q": q.strip(), "lines": lines, "line_choices": logs.LINE_CHOICES,
    })


@router.get("/download")
def logs_download():
    return Response(logs.raw(), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="highlightarr.log"'})
