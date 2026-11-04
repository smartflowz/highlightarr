"""The app's own version. Bump `__version__` when you release; the commit is filled in by the
Docker build (see the GIT_COMMIT build argument) and is empty when running from source."""
import os

__version__ = "0.1.0"
COMMIT = os.getenv("GIT_COMMIT", "")[:7]
