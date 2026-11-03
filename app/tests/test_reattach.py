from nvr.db import init_db
import nvr.db as db_module
from nvr.models import Camera
from nvr.reattach import reattach_from_scanned_hosts
from nvr.security import encrypt_secret


def test_reattach_updates_missing_camera_from_free_host(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="Sala",
            host="10.0.0.1",
            username="u1",
            password_enc=encrypt_secret("p1"),
        )
        other = Camera(
            name="Garagem",
            host="10.0.0.2",
            username="u2",
            password_enc=encrypt_secret("p2"),
        )
        db.add_all([cam, other])
        db.commit()

        # Scan sees other still online + a free host; missing cam's old IP absent
        hosts = ["10.0.0.2", "10.0.0.50"]

        def auth_first(hosts_list, username, password, port):
            if username == "u1" and "10.0.0.50" in hosts_list:
                return "10.0.0.50"
            return None

        result = reattach_from_scanned_hosts(
            db, hosts, port=554, auth_first_fn=auth_first
        )
        db.refresh(cam)
        db.refresh(other)
        assert cam.host == "10.0.0.50"
        assert other.host == "10.0.0.2"
        assert len(result["updated"]) == 1
        assert result["updated"][0]["name"] == "Sala"
        assert any(s["name"] == "Garagem" for s in result["skipped"])
    finally:
        db.close()


def test_reattach_endpoint(auth_client, monkeypatch):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="Hall",
            host="10.0.0.9",
            username="admin",
            password_enc=encrypt_secret("secret"),
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id
    finally:
        db.close()

    monkeypatch.setattr(
        "nvr.routers.web.reattach_from_scanned_hosts",
        lambda db, hosts, port=554: {
            "updated": [
                {
                    "camera_id": cam_id,
                    "name": "Hall",
                    "old_host": "10.0.0.9",
                    "host": "10.0.0.77",
                }
            ],
            "unmatched": [],
            "skipped": [],
            "free_remaining": [],
        },
    )
    monkeypatch.setattr("nvr.routers.web.sync_go2rtc", lambda db: None)
    monkeypatch.setattr("nvr.routers.web.recorder_manager.reconcile", lambda db: None)

    r = auth_client.post(
        "/cameras/discover/reattach",
        json={"hosts": ["10.0.0.77"], "port": 554},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"][0]["host"] == "10.0.0.77"
