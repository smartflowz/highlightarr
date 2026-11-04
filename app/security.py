"""The app has no login, so any web page you visit could make your browser
POST to it (create a workflow, delete things). Browsers label such requests
with an Origin header; refuse state-changing requests whose Origin is not us.
Requests without an Origin (curl, scripts, tests) are not affected."""
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_cross_origin(method: str, headers) -> bool:
    origin = headers.get("origin")
    if method in SAFE_METHODS or origin is None:
        return False
    # X-Forwarded-Host covers a reverse proxy that rewrites Host.
    ours = {headers.get("host"), headers.get("x-forwarded-host")} - {None}
    return urlsplit(origin).netloc not in ours


class SameOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if is_cross_origin(request.method, request.headers):
            return PlainTextResponse("Cross-origin request blocked.", status_code=403)
        return await call_next(request)
