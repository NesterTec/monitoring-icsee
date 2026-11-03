from datetime import datetime, timedelta

from nvr.db import init_db
import nvr.db as db_module
from nvr.models import Camera, Notification
from nvr.security import encrypt_secret


def test_camera_reachability_columns_and_notification(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="Hall",
            host="192.168.1.10",
            username="admin",
            password_enc=encrypt_secret("x"),
            reachability_status="ok",
            fail_count=0,
        )
        db.add(cam)
        db.commit()
        db.refresh(cam)
        assert cam.reachability_status == "ok"
        assert cam.fail_count == 0
        assert cam.last_seen_at is None
        n = Notification(
            camera_id=cam.id,
            kind="camera_lost",
            message="Dispositivo Hall foi perdido",
            active=True,
            created_at=datetime.now(),
        )
        db.add(n)
        db.commit()
        assert db.query(Notification).filter_by(camera_id=cam.id, active=True).count() == 1
    finally:
        db.close()


def test_three_failures_triggers_rediscovery_flag():
    from nvr.camera_monitor import apply_health_failure

    cam = Camera(name="A", host="10.0.0.1", username="u", password_enc="x", fail_count=0)
    assert apply_health_failure(cam) is False
    assert apply_health_failure(cam) is False
    assert apply_health_failure(cam) is True
    assert cam.fail_count == 3
    assert cam.reachability_status == "unreachable"


def test_rediscover_updates_host_first_free_match(app_env):
    from nvr.camera_monitor import rediscover_camera
    from nvr.health_logs import list_recent

    init_db()
    db = db_module.SessionLocal()
    try:
        other = Camera(name="B", host="10.0.0.2", username="u", password_enc=encrypt_secret("p"))
        cam = Camera(
            name="A",
            host="10.0.0.1",
            username="u",
            password_enc=encrypt_secret("p"),
            fail_count=3,
        )
        db.add_all([other, cam])
        db.commit()

        def scan_fn(cidr, port=554):
            return ["10.0.0.2", "10.0.0.3", "10.0.0.4"]

        def auth_first(hosts, username, password, port):
            assert "10.0.0.2" not in hosts
            assert "10.0.0.1" not in hosts
            return hosts[0] if hosts else None

        result = rediscover_camera(
            db, cam, scan_fn=scan_fn, auth_first_fn=auth_first, now=datetime.now(), force=True
        )
        assert result == "updated"
        db.refresh(cam)
        assert cam.host == "10.0.0.3"
        assert cam.reachability_status == "ok"
        logs = list_recent(db)
        assert any(l.event == "rediscovery" and l.level == "ok" for l in logs)
    finally:
        db.close()


def test_rediscover_marks_lost_when_no_match(app_env):
    from nvr.camera_monitor import rediscover_camera
    from nvr.health_logs import list_recent

    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="Hall",
            host="10.0.0.1",
            username="u",
            password_enc=encrypt_secret("p"),
            fail_count=3,
        )
        db.add(cam)
        db.commit()
        result = rediscover_camera(
            db,
            cam,
            scan_fn=lambda cidr, port=554: ["10.0.0.9"],
            auth_first_fn=lambda *a, **k: None,
            now=datetime.now(),
            force=True,
        )
        assert result == "lost"
        db.refresh(cam)
        assert cam.reachability_status == "lost"
        logs = list_recent(db)
        assert any(l.event == "rediscovery" and l.level == "error" and "Hall" == l.camera_name for l in logs)
    finally:
        db.close()


def test_rediscover_respects_cooldown(app_env):
    from nvr.camera_monitor import rediscover_camera

    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="A",
            host="10.0.0.1",
            username="u",
            password_enc=encrypt_secret("p"),
            fail_count=3,
            last_scan_at=datetime.now(),
        )
        db.add(cam)
        db.commit()
        result = rediscover_camera(
            db,
            cam,
            scan_fn=lambda *a, **k: [],
            auth_first_fn=lambda *a, **k: None,
            now=datetime.now() + timedelta(seconds=30),
            force=False,
        )
        assert result == "skipped"
    finally:
        db.close()


def test_tick_success_resets_fail_count(app_env, monkeypatch):
    from nvr import camera_monitor as cm

    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(
            name="A",
            host="10.0.0.1",
            username="u",
            password_enc=encrypt_secret("p"),
            fail_count=2,
            reachability_status="unreachable",
        )
        db.add(cam)
        db.commit()
        monkeypatch.setattr(cm, "check_camera_tcp", lambda host, port, timeout=0.5: True)
        cm.camera_monitor.tick(db)
        db.refresh(cam)
        assert cam.fail_count == 0
        assert cam.reachability_status == "ok"
        assert cam.last_seen_at is not None
    finally:
        db.close()
