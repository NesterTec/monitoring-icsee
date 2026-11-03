from nvr.db import init_db
import nvr.db as db_module
from nvr.models import Camera
from nvr.notifications import clear_camera_lost, ensure_camera_lost, list_active
from nvr.security import encrypt_secret


def test_ensure_camera_lost_is_idempotent(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(name="A", host="1.1.1.1", username="u", password_enc=encrypt_secret("p"))
        db.add(cam)
        db.commit()
        n1 = ensure_camera_lost(db, cam)
        n2 = ensure_camera_lost(db, cam)
        assert n1.id == n2.id
        assert len(list_active(db)) == 1
        assert n1.message == "Dispositivo A foi perdido"
        clear_camera_lost(db, cam.id)
        assert list_active(db) == []
    finally:
        db.close()
