from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import paths

router = APIRouter()


@router.get("/api/browse")
def api_browse(path: str = ""):
    try:
        return paths.browse(path)
    except paths.BrowseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
