"""FastAPI dependencies shared by the routers."""
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from .db import get_session

SessionDep = Annotated[Session, Depends(get_session)]


async def _form_data(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


FormDep = Annotated[dict[str, str], Depends(_form_data)]
