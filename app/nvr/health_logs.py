from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from nvr.models import HealthLog

MAX_LOGS = 30


def append_health_log(
    db: Session,
    *,
    level: str,
    message: str,
    camera_id: int | None = None,
    camera_name: str | None = None,
    host: str | None = None,
    event: str = "ping",
) -> HealthLog:
    row = HealthLog(
        created_at=datetime.now(),
        level=level,
        event=event,
        camera_id=camera_id,
        camera_name=camera_name,
        host=host,
        message=message,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _prune(db)
    return row


def _prune(db: Session) -> None:
    ids = (
        db.query(HealthLog.id)
        .order_by(HealthLog.id.desc())
        .offset(MAX_LOGS)
        .all()
    )
    if not ids:
        return
    old_ids = [i for (i,) in ids]
    db.query(HealthLog).filter(HealthLog.id.in_(old_ids)).delete(synchronize_session=False)
    db.commit()


def list_recent(
    db: Session,
    limit: int = 200,
    *,
    camera_ids: list[int] | None = None,
) -> list[HealthLog]:
    q = db.query(HealthLog)
    if camera_ids is not None:
        if not camera_ids:
            return []
        q = q.filter(HealthLog.camera_id.in_(camera_ids))
    return q.order_by(HealthLog.id.desc()).limit(limit).all()
