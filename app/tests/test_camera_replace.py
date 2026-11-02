from nvr.db import init_db
import nvr.db as db_module
from nvr.models import Camera, RecordingSegment
from nvr.security import decrypt_secret, encrypt_secret
from datetime import datetime


def test_replace_camera_keeps_id(auth_client, monkeypatch):
    monkeypatch.setattr(
        "nvr.routers.web.probe_rtsp_auth",
        lambda *a, **k: (True, "ok"),
    )
    r = auth_client.post(
        "/cameras/create",
        data={
            "name": "OldCam",
            "host": "10.0.0.1",
            "username": "olduser",
            "password": "oldpass",
            "rtsp_port": "554",
            "channel": "1",
            "stream_sub": "1",
            "stream_main": "0",
            "enabled": "on",
            "recording_enabled": "on",
            "dashboard_visible": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    init_db()
    db = db_module.SessionLocal()
    try:
        cam = db.query(Camera).filter_by(name="OldCam").one()
        cam_id = cam.id
        db.add(
            RecordingSegment(
                camera_id=cam_id,
                path="/tmp/x.mp4",
                started_at=datetime.now(),
                size_bytes=10,
            )
        )
        db.commit()
    finally:
        db.close()

    r = auth_client.post(
        f"/cameras/{cam_id}/replace",
        data={
            "host": "10.0.0.99",
            "username": "newuser",
            "password": "newpass",
            "rtsp_port": "554",
            "channel": "1",
            "stream_sub": "1",
            "stream_main": "0",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    db = db_module.SessionLocal()
    try:
        cam = db.get(Camera, cam_id)
        assert cam is not None
        assert cam.name == "OldCam"
        assert cam.host == "10.0.0.99"
        assert cam.username == "newuser"
        assert decrypt_secret(cam.password_enc) == "newpass"
        assert cam.reachability_status == "ok"
        assert db.query(RecordingSegment).filter_by(camera_id=cam_id).count() == 1
    finally:
        db.close()


def test_retry_reachability_json(auth_client, monkeypatch):
    auth_client.post(
        "/cameras/create",
        data={
            "name": "RetryCam",
            "host": "10.0.0.5",
            "username": "u",
            "password": "p",
            "rtsp_port": "554",
            "channel": "1",
            "stream_sub": "1",
            "stream_main": "0",
            "enabled": "on",
            "recording_enabled": "on",
            "dashboard_visible": "on",
        },
        follow_redirects=False,
    )
    db = db_module.SessionLocal()
    try:
        cam = db.query(Camera).filter_by(name="RetryCam").one()
        cam_id = cam.id
        cam.reachability_status = "lost"
        cam.fail_count = 3
        db.commit()
    finally:
        db.close()

    def fake_rediscover(db, cam, force=False):
        cam.host = "10.0.0.50"
        cam.reachability_status = "ok"
        cam.fail_count = 0
        db.commit()
        return "updated"

    monkeypatch.setattr("nvr.routers.web.rediscover_camera", fake_rediscover)

    r = auth_client.post(f"/cameras/{cam_id}/retry-reachability")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["result"] == "updated"
    assert body["host"] == "10.0.0.50"
