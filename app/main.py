import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, db, logs, worker
from .routers import about, activity, browse, logs_page, profiles, settings_page, workflows
from .security import SameOriginMiddleware
from .version import __version__

logging.basicConfig(level=logging.INFO, format=logs.FORMAT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup()
    db.init_db()
    db.seed_defaults()
    task = asyncio.create_task(worker.worker_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logs.teardown()


app = FastAPI(title="Highlightarr", version=__version__, lifespan=lifespan)
app.add_middleware(SameOriginMiddleware)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
for module in (activity, profiles, workflows, settings_page, about, logs_page, browse):
    app.include_router(module.router)
