# Camera DHCP Rediscovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep cameras reachable under DHCP by periodic RTSP TCP health checks, credential-based LAN rediscovery that updates `host` in place, lost-state notifications (global modal + bell), manual retry, and same-record “Substituir dispositivo”.

**Architecture:** A daemon thread `camera_monitor` (same pattern as `recorder_manager`) ticks every ~30s, TCP-probes each enabled camera, and after 3 failures runs scan+auth reuse from `nvr.discover`. Persistence adds reachability fields on `Camera` plus a `notifications` table. UI hooks live in `base.html` / `camera_form.html` with small JSON/POST routes.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2 + SQLite, Jinja2, existing `discover.py` / `go2rtc_sync` / `recorder_manager`, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-02-camera-dhcp-rediscovery-design.md`
- Health: TCP RTSP every ~30s; rediscovery after 3 consecutive failures
- Match: first free host that authenticates (`as_completed`); skip hosts owned by other cameras
- Full LAN scan cooldown: ≥2 minutes per camera
- Same `Camera.id` forever for recordings/live; only update `host` (and credentials on replace)
- Do not commit `.env` secrets; Portuguese UI copy
- TDD per task; frequent commits

## File map

| Path | Responsibility |
|------|----------------|
| `app/nvr/models.py` | `Camera` reachability fields + `Notification` |
| `app/nvr/db.py` | SQLite `ALTER` for new camera columns |
| `app/nvr/discover.py` | Export/reuse `_tcp_open`; optional `first_auth_match` helper |
| `app/nvr/camera_monitor.py` | Background health + rediscovery loop |
| `app/nvr/notifications.py` | Create/deactivate/list active notifications |
| `app/nvr/main.py` | Start/stop monitor in lifespan |
| `app/nvr/routers/web.py` | notifications API, retry, replace |
| `app/nvr/templates/base.html` | Bell + modal + poll script |
| `app/nvr/templates/camera_form.html` | Retry + replace UI |
| `app/nvr/static/style.css` | Bell/modal styles |
| `app/tests/test_camera_monitor.py` | Health/rediscovery unit tests |
| `app/tests/test_notifications.py` | Notification + HTTP tests |
| `app/tests/test_camera_replace.py` | Replace / retry endpoints |

---

### Task 1: Model + migration for reachability and notifications

**Files:**
- Modify: `app/nvr/models.py`
- Modify: `app/nvr/db.py`
- Test: `app/tests/test_camera_monitor.py` (migration/create smoke)

**Interfaces:**
- Produces: `Camera.reachability_status: str` default `"ok"`; `fail_count: int` default `0`; `last_seen_at: datetime | None`; `last_scan_at: datetime | None`
- Produces: `Notification` with `id`, `camera_id`, `kind`, `message`, `created_at`, `active: bool`

- [ ] **Step 1: Write failing test that new columns/table exist after init_db**

```python
# app/tests/test_camera_monitor.py
from datetime import datetime

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
```

- [ ] **Step 2: Run test — expect fail**

Run: `cd app && python -m pytest tests/test_camera_monitor.py::test_camera_reachability_columns_and_notification -v`

- [ ] **Step 3: Implement models + migration**

Add fields to `Camera` and new `Notification` model. In `_migrate_sqlite`, `ALTER TABLE` for `reachability_status`, `fail_count`, `last_seen_at`, `last_scan_at` if missing. `create_all` creates `notifications`.

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add app/nvr/models.py app/nvr/db.py app/tests/test_camera_monitor.py
git commit -m "feat: add camera reachability fields and notifications table"
```

---

### Task 2: Notifications service helpers

**Files:**
- Create: `app/nvr/notifications.py`
- Test: `app/tests/test_notifications.py`

**Interfaces:**
- Produces: `ensure_camera_lost(db, cam: Camera) -> Notification`
- Produces: `clear_camera_lost(db, camera_id: int) -> None`
- Produces: `list_active(db) -> list[Notification]`

- [ ] **Step 1: Failing tests**

```python
def test_ensure_camera_lost_is_idempotent(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        cam = Camera(name="A", host="1.1.1.1", username="u", password_enc=encrypt_secret("p"))
        db.add(cam)
        db.commit()
        from nvr.notifications import ensure_camera_lost, clear_camera_lost, list_active
        n1 = ensure_camera_lost(db, cam)
        n2 = ensure_camera_lost(db, cam)
        assert n1.id == n2.id
        assert len(list_active(db)) == 1
        clear_camera_lost(db, cam.id)
        assert list_active(db) == []
    finally:
        db.close()
```

- [ ] **Step 2–4: Implement helpers; pass tests; commit**

Message format: `Dispositivo {cam.name} foi perdido`

---

### Task 3: Rediscovery pure logic (testable without thread)

**Files:**
- Create: `app/nvr/camera_monitor.py` (functions first)
- Modify: `app/nvr/discover.py` if needed (`tcp_open` public alias)
- Test: `app/tests/test_camera_monitor.py`

**Interfaces:**
- Produces: `HEALTH_INTERVAL_SEC = 30`, `FAIL_THRESHOLD = 3`, `SCAN_COOLDOWN_SEC = 120`
- Produces: `check_camera_tcp(host, port, timeout=0.5) -> bool`
- Produces: `apply_health_success(cam) -> None` (mutates cam fields)
- Produces: `apply_health_failure(cam) -> bool` (returns True if should attempt rediscovery)
- Produces: `rediscover_camera(db, cam, *, scan_fn, auth_first_fn, now) -> str`  
  Returns `"updated" | "lost" | "skipped"`  
  `auth_first_fn(hosts, username, password, port) -> str | None` (first matching host)

- [ ] **Step 1: Tests with mocks**

```python
def test_three_failures_triggers_rediscovery_flag():
    cam = Camera(name="A", host="10.0.0.1", username="u", password_enc="x", fail_count=0)
    assert apply_health_failure(cam) is False
    assert apply_health_failure(cam) is False
    assert apply_health_failure(cam) is True
    assert cam.fail_count == 3
    assert cam.reachability_status == "unreachable"


def test_rediscover_updates_host_first_free_match(app_env):
    init_db()
    db = db_module.SessionLocal()
    try:
        other = Camera(name="B", host="10.0.0.2", username="u", password_enc=encrypt_secret("p"))
        cam = Camera(name="A", host="10.0.0.1", username="u", password_enc=encrypt_secret("p"), fail_count=3)
        db.add_all([other, cam])
        db.commit()
        def scan_fn(cidr, port=554):
            return ["10.0.0.2", "10.0.0.3", "10.0.0.4"]
        def auth_first(hosts, username, password, port):
            assert "10.0.0.2" not in hosts  # owned by other
            return hosts[0] if hosts else None
        from nvr.camera_monitor import rediscover_camera
        result = rediscover_camera(db, cam, scan_fn=scan_fn, auth_first_fn=auth_first, now=datetime.now())
        assert result == "updated"
        db.refresh(cam)
        assert cam.host == "10.0.0.3"
        assert cam.reachability_status == "ok"
    finally:
        db.close()


def test_rediscover_marks_lost_when_no_match(app_env):
    ...
    result = rediscover_camera(..., auth_first_fn=lambda *a, **k: None, ...)
    assert result == "lost"
    assert cam.reachability_status == "lost"
```

- [ ] **Step 2–4: Implement; pass; commit**

`rediscover_camera` must: respect `last_scan_at` cooldown; exclude other cameras' hosts; on update call `clear_camera_lost`; on lost call `ensure_camera_lost`; set `last_scan_at`.

---

### Task 4: Background CameraMonitor + lifespan

**Files:**
- Modify: `app/nvr/camera_monitor.py` (class + loop)
- Modify: `app/nvr/main.py`
- Test: tick with monkeypatched sleep/check (unit)

**Interfaces:**
- Produces: `camera_monitor = CameraMonitor()` with `start_background()`, `stop()`, `tick(db)`  
- `tick` walks enabled cameras, TCP check, success/failure, rediscovery when flagged; after host update calls `sync_go2rtc(db)` and `recorder_manager.reconcile(db)`

- [ ] **Step 1: Test `tick` success path resets fail_count (mock TCP)**

- [ ] **Step 2–4: Implement loop (`time.sleep` with stop event); wire lifespan; commit**

In tests, do **not** require the background thread; call `tick` directly. Lifespan: `camera_monitor.start_background()` after recorder; on shutdown `camera_monitor.stop()`.

---

### Task 5: HTTP — list notifications, retry, replace

**Files:**
- Modify: `app/nvr/routers/web.py`
- Test: `app/tests/test_notifications.py`, `app/tests/test_camera_replace.py`

**Interfaces:**
- `GET /notifications` → `{"items":[{"id","camera_id","kind","message","created_at"}]}` (auth)
- `POST /cameras/{id}/retry-reachability` → runs one failure-threshold bypass rediscovery (force scan) → JSON `{ok, result, host?}`
- `POST /cameras/{id}/replace` form/JSON: `host`, `username`, `password`, optional RTSP fields → validate with `probe_rtsp_auth` → update same row → clear lost → sync/reconcile → redirect or JSON

- [ ] **Step 1: Failing auth_client tests**

```python
def test_notifications_empty(auth_client):
    r = auth_client.get("/notifications")
    assert r.status_code == 200
    assert r.json()["items"] == []

def test_replace_camera_keeps_id(auth_client, monkeypatch):
    # create camera via /cameras/create, monkeypatch probe_rtsp_auth True
    # POST replace with new host/user/pass
    # assert same id, new host/username, segments still attached if any
```

- [ ] **Step 2–4: Implement routes; pass; commit**

Retry: set `fail_count` high / ignore cooldown (`force=True` on `rediscover_camera`).

---

### Task 6: UI — bell, modal, form actions

**Files:**
- Modify: `app/nvr/templates/base.html`
- Modify: `app/nvr/templates/camera_form.html`
- Modify: `app/nvr/static/style.css` (bump `?v=` on stylesheet)

**Interfaces:**
- Header bell button + dropdown listing active notifications
- Modal `#camera-lost-modal` shown once per notification id per browser tab (`sessionStorage` key `icsee_seen_notif_{id}`)
- Edit form: button “Tentar novamente” → `POST /cameras/{id}/retry-reachability`; section “Substituir dispositivo” with host/user/pass → `POST /cameras/{id}/replace`

- [ ] **Step 1: Implement UI + minimal CSS**
- [ ] **Step 2: Manual smoke or fetch `/notifications` from logged-in page script**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat: notify lost cameras and support retry/replace in UI"
```

---

### Task 7: Spec self-check + full pytest

- [ ] Run: `cd app && python -m pytest -v`
- [ ] Confirm coverage vs spec: health 30s, 3 fails, first free match, cooldown 2m, lost modal+bell, retry, replace same id
- [ ] Final commit if any fixes

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| TCP health ~30s | 3–4 |
| 3 failures → rediscovery | 3 |
| First free auth match | 3 |
| Skip other cameras' IPs | 3 |
| Update same Camera.host | 3–5 |
| lost + notification | 2–3 |
| Global modal + bell | 6 |
| Retry in edit | 5–6 |
| Substituir dispositivo | 5–6 |
| sync_go2rtc + reconcile | 4–5 |
| Scan cooldown ≥2 min | 3 |
