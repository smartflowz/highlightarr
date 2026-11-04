from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

from . import config

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def localtime(value: datetime) -> datetime:
    """Stored timestamps are naive UTC; show them in the container's time zone (the TZ variable)."""
    return value.replace(tzinfo=UTC).astimezone()


templates.env.filters["localtime"] = localtime
templates.env.globals["supported_formats"] = ", ".join(config.VIDEO_EXTENSIONS)
