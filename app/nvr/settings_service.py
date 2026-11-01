from pathlib import Path

from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.models import AppSetting


STORAGE_KEY = "storage_path"
QUOTA_KEY = "quota_gb"


def _get(db: Session, key: str) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


def ensure_defaults(db: Session) -> None:
    settings = get_settings()
    if _get(db, STORAGE_KEY) is None:
        _set(db, STORAGE_KEY, settings.default_storage_path)
    if _get(db, QUOTA_KEY) is None:
        _set(db, QUOTA_KEY, str(settings.quota_gb))


def get_storage_path(db: Session) -> Path:
    ensure_defaults(db)
    path = Path(_get(db, STORAGE_KEY) or get_settings().default_storage_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_quota_gb(db: Session) -> float:
    ensure_defaults(db)
    return float(_get(db, QUOTA_KEY) or get_settings().quota_gb)


def set_storage_path(db: Session, path: str) -> Path:
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    _set(db, STORAGE_KEY, str(p))
    return p


def set_quota_gb(db: Session, quota_gb: float) -> None:
    if quota_gb <= 0:
        raise ValueError("quota must be positive")
    _set(db, QUOTA_KEY, str(quota_gb))
