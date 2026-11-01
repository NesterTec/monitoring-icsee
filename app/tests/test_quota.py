from datetime import datetime, timedelta
from pathlib import Path

import nvr.db as db_module
from nvr.db import init_db
from nvr.models import Camera, RecordingSegment
from nvr.quota import enforce_quota
from nvr.security import encrypt_secret


def test_enforce_quota_deletes_oldest(app_env, tmp_path):
    init_db()
    db = db_module.SessionLocal()
    storage = Path(app_env) / "recordings"

    cam = Camera(name="c", host="1.1.1.1", username="u", password_enc=encrypt_secret("p"))
    db.add(cam)
    db.commit()
    db.refresh(cam)

    files = []
    for i in range(3):
        f = storage / f"old{i}.mp4"
        f.write_bytes(b"x" * 5000)
        files.append(f)
        db.add(
            RecordingSegment(
                camera_id=cam.id,
                path=str(f),
                started_at=datetime.now() - timedelta(hours=3 - i),
                size_bytes=5000,
            )
        )
    db.commit()

    deleted = enforce_quota(db, storage, quota_gb=0.000001, reserve_bytes=0)
    assert deleted
    assert not Path(deleted[0]).exists()
    db.close()
