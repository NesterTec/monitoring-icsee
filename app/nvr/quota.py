import os
from pathlib import Path

from sqlalchemy.orm import Session

from nvr.models import RecordingSegment


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def free_bytes(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def enforce_quota(
    db: Session,
    storage_path: Path,
    quota_gb: float,
    reserve_bytes: int,
) -> list[str]:
    quota_bytes = int(quota_gb * 1024**3)
    deleted: list[str] = []

    def over_limit() -> bool:
        used = dir_size(storage_path)
        free = free_bytes(storage_path)
        return used >= quota_bytes or free < reserve_bytes

    while over_limit():
        seg = (
            db.query(RecordingSegment)
            .order_by(RecordingSegment.started_at.asc())
            .first()
        )
        if seg is None:
            break
        try:
            Path(seg.path).unlink(missing_ok=True)
        except OSError:
            pass
        deleted.append(seg.path)
        db.delete(seg)
        db.commit()

    return deleted
