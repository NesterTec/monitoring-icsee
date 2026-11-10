from __future__ import annotations

from nvr.config import get_settings


def public_base_path() -> str:
    raw = (get_settings().public_base_path or "").strip()
    if not raw or raw == "/":
        return ""
    return raw.rstrip("/")


def abs_path(path: str) -> str:
    """Prefix an app-absolute path with PUBLIC_BASE_PATH when set."""
    if not path.startswith("/"):
        path = "/" + path
    base = public_base_path()
    if not base:
        return path
    if path == "/":
        return base + "/"
    return base + path
