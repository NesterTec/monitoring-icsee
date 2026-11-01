import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Configure env before importing app modules that cache settings
@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    storage = tmp_path / "recordings"
    storage.mkdir()
    cfg = tmp_path / "go2rtc.yaml"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("DEFAULT_STORAGE_PATH", str(storage))
    monkeypatch.setenv("GO2RTC_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("ADMIN_EMAIL", "ivanrank@yahoo.com.br")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("CAMERA_SECRET_KEY", "test-camera-secret-key-material")
    from nvr.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def client(app_env):
    from nvr.db import init_db
    from nvr.main import create_app

    init_db()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    r = client.post(
        "/login",
        data={"email": "ivanrank@yahoo.com.br", "password": "secret"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    return client
