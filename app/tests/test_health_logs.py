from nvr.db import init_db
import nvr.db as db_module
from nvr.health_logs import append_health_log, list_recent
from nvr.models import Camera
from nvr.security import encrypt_secret


def test_append_and_list_health_logs(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(name="Sala", host="10.0.0.1", username="u", password_enc=encrypt_secret("p"))
        db.add(cam)
        db.commit()
        append_health_log(
            db,
            level="ok",
            event="ping",
            camera_id=cam.id,
            camera_name=cam.name,
            host=cam.host,
            message="TCP OK",
        )
        rows = list_recent(db)
        assert len(rows) == 1
        assert rows[0].level == "ok"
        assert rows[0].camera_name == "Sala"
    finally:
        db.close()


def test_logs_page(auth_client):
    r = auth_client.get("/logs")
    assert r.status_code == 200
    assert "Logs" in r.text
