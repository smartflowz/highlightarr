import os
import sys
import tempfile
from pathlib import Path

# Make `import app` work however pytest is started (`pytest`, `python -m pytest`, from any folder).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Configuration is read at import time, so point it at a scratch area first.
_ROOT = tempfile.mkdtemp(prefix="highlightarr-test-")
os.environ["DB_PATH"] = f"{_ROOT}/config/test.db"
os.environ["MEDIA_ROOTS"] = f"{_ROOT}/media"
os.makedirs(f"{_ROOT}/media/in/sub", exist_ok=True)
os.makedirs(f"{_ROOT}/media/out", exist_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import db, main, settings, worker  # noqa: E402

MEDIA = f"{_ROOT}/media"


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Empty tables for every test, no seeded profiles, no background worker."""
    db.init_db()                           # creates the folder + tables on first use
    SQLModel.metadata.drop_all(db.engine)
    SQLModel.metadata.create_all(db.engine)
    settings._cache = None
    worker._seen.clear()
    worker._done.clear()

    async def idle():
        return None

    monkeypatch.setattr(main.worker, "worker_loop", idle)
    monkeypatch.setattr(db, "seed_defaults", lambda: None)   # tests start with no profiles


@pytest.fixture
def client():
    with TestClient(main.app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def media():
    return MEDIA
