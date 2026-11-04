"""Everything about which folders the app may touch, plus the folder
browser behind the Browse button."""
import os

from . import config

# Skipped when browsing/scanning: dotfolders, Synology @eaDir/@tmp, #recycle.
HIDDEN_PREFIXES = (".", "@", "#recycle")


class BrowseError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def media_roots() -> list[str]:
    return [os.path.realpath(r) for r in config.MEDIA_ROOTS if os.path.isdir(r)]


def is_within(path: str, parent: str) -> bool:
    """True if `path` is `parent` or below it (symlinks resolved)."""
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def is_within_media(path: str) -> bool:
    return any(is_within(path, root) for root in media_roots())


def browse(path: str = "") -> dict:
    """List the sub-folders of `path`. Never leaves the media roots: a path
    that is outside them, or does not exist yet, falls back to the closest
    folder that is valid (ultimately the first root)."""
    roots = media_roots()
    if not roots:
        raise BrowseError(
            f"No media folder is mounted (looked for: {', '.join(config.MEDIA_ROOTS)}).", 404)
    cur = os.path.realpath(path) if path else roots[0]
    while not os.path.isdir(cur) and cur != os.path.dirname(cur):
        cur = os.path.dirname(cur)
    if not any(is_within(cur, r) for r in roots):
        cur = roots[0]
    try:
        names = [e.name for e in os.scandir(cur)
                 if e.is_dir() and not e.name.startswith(HIDDEN_PREFIXES)
                 and any(is_within(e.path, r) for r in roots)]   # symlinks may lead outside
    except PermissionError:
        raise BrowseError(f"No permission to read {cur}.", 403) from None
    parent = os.path.dirname(cur)
    can_go_up = cur not in roots and any(is_within(parent, r) for r in roots)
    return {
        "path": cur,
        "parent": parent if can_go_up else None,
        "dirs": [{"name": n, "path": os.path.join(cur, n)} for n in sorted(names, key=str.lower)],
    }
