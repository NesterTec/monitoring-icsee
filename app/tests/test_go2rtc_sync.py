from pathlib import Path

import nvr.db as db_module
from nvr.db import init_db
from nvr.go2rtc_sync import sync_go2rtc
from nvr.models import Camera
from nvr.security import encrypt_secret


def test_sync_writes_stream(app_env):
    init_db()
    db = db_module.SessionLocal()
    db.add(
        Camera(
            name="Portao",
            host="192.168.1.15",
            username="ahkn",
            password_enc=encrypt_secret("ar2aha"),
            enabled=True,
        )
    )
    db.commit()
    path = sync_go2rtc(db)
    text = Path(path).read_text()
    assert "cam_1" in text or "cam_" in text
    assert "192.168.1.15" in text
    db.close()
