from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from nvr.models import Camera, Notification


def ensure_camera_lost(db: Session, cam: Camera) -> Notification:
    existing = (
        db.query(Notification)
        .filter(
            Notification.camera_id == cam.id,
            Notification.kind == "camera_lost",
            Notification.active.is_(True),
        )
        .first()
    )
    if existing is not None:
        return existing
    n = Notification(
        camera_id=cam.id,
        kind="camera_lost",
        message=f"Dispositivo {cam.name} foi perdido",
        created_at=datetime.now(),
        active=True,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def clear_camera_lost(db: Session, camera_id: int) -> None:
    rows = (
        db.query(Notification)
        .filter(
            Notification.camera_id == camera_id,
            Notification.kind == "camera_lost",
            Notification.active.is_(True),
        )
        .all()
    )
    for row in rows:
        row.active = False
    if rows:
        db.commit()


def list_active(db: Session) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.active.is_(True))
        .order_by(Notification.created_at.desc())
        .all()
    )
