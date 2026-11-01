import nvr.db as db_module
from nvr.models import Camera
from nvr.security import decrypt_secret


def test_create_camera_encrypts_password(auth_client):
    r = auth_client.post(
        "/cameras/create",
        data={
            "name": "Portao",
            "host": "192.168.1.15",
            "username": "ahkn",
            "password": "ar2aha",
            "rtsp_port": "554",
            "channel": "1",
            "stream_sub": "1",
            "stream_main": "0",
            "enabled": "on",
            "recording_enabled": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    db = db_module.SessionLocal()
    try:
        cam = db.query(Camera).one()
        assert "ar2aha" not in cam.password_enc
        assert decrypt_secret(cam.password_enc) == "ar2aha"
    finally:
        db.close()
