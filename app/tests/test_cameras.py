from datetime import datetime

import nvr.db as db_module
from nvr.models import Camera, RecordingSegment
from nvr.security import decrypt_secret, encrypt_secret


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


def test_delete_camera_with_segments(auth_client):
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="Temp",
            host="192.168.1.99",
            username="u",
            password_enc=encrypt_secret("p"),
            sort_order=0,
        )
        db.add(cam)
        db.flush()
        db.add(
            RecordingSegment(
                camera_id=cam.id,
                path="/tmp/fake.mp4",
                started_at=datetime.now(),
                size_bytes=10,
            )
        )
        db.commit()
        cam_id = cam.id
    finally:
        db.close()

    r = auth_client.post(f"/cameras/{cam_id}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)

    db = db_module.SessionLocal()
    try:
        assert db.get(Camera, cam_id) is None
        assert db.query(RecordingSegment).filter_by(camera_id=cam_id).count() == 0
    finally:
        db.close()
