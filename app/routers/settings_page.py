from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import settings
from ..deps import FormDep
from ..templating import templates

router = APIRouter(prefix="/settings")


def _page(request: Request, status_code: int = 200, **extra):
    rows = [{"key": key, "spec": spec, "value": settings.get(key),
             "overridden": settings.is_overridden(key)}
            for key, spec in settings.SPEC.items()]
    return templates.TemplateResponse(request, "settings.html", {"rows": rows, **extra},
                                      status_code=status_code)


@router.get("")
def settings_page(request: Request, saved: int = 0):
    return _page(request, saved=bool(saved))


@router.post("/save")
def settings_save(request: Request, form: FormDep):
    values, errors = settings.parse_form(form)
    if errors:
        return _page(request, 400, errors=errors, submitted=form)
    settings.save(values)
    return RedirectResponse("/settings?saved=1", status_code=303)
