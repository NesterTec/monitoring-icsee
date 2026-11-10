from nvr.config import get_settings
from nvr.urls import abs_path, public_base_path


def test_abs_path_empty_by_default(app_env):
    get_settings.cache_clear()
    assert public_base_path() == ""
    assert abs_path("/cameras") == "/cameras"
    assert abs_path("/") == "/"


def test_abs_path_with_prefix(app_env, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_PATH", "/adm/house/monitor")
    get_settings.cache_clear()
    assert public_base_path() == "/adm/house/monitor"
    assert abs_path("/cameras") == "/adm/house/monitor/cameras"
    assert abs_path("/") == "/adm/house/monitor/"
    assert abs_path("logs") == "/adm/house/monitor/logs"
    get_settings.cache_clear()


def test_login_redirect_uses_prefix(auth_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_PATH", "/adm/house/monitor")
    get_settings.cache_clear()
    # Recreate client after settings change
    from fastapi.testclient import TestClient
    from nvr.db import init_db
    from nvr.main import create_app
    from nvr.camera_monitor import camera_monitor

    camera_monitor.enabled = False
    init_db()
    with TestClient(create_app()) as client:
        r = client.post(
            "/login",
            data={"email": "ivanrank@yahoo.com.br", "password": "secret"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        assert r.headers["location"] == "/adm/house/monitor/"
    get_settings.cache_clear()
