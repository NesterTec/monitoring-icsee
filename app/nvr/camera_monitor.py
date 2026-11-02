from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy.orm import Session

import nvr.db as db_module
from nvr.discover import guess_default_cidr, probe_rtsp_auth, scan_rtsp_hosts, tcp_open
from nvr.go2rtc_sync import sync_go2rtc
from nvr.models import Camera
from nvr.notifications import clear_camera_lost, ensure_camera_lost
from nvr.recorder import recorder_manager
from nvr.security import decrypt_secret

logger = logging.getLogger(__name__)

HEALTH_INTERVAL_SEC = 30
FAIL_THRESHOLD = 3
SCAN_COOLDOWN_SEC = 120
TCP_TIMEOUT = 0.5


def check_camera_tcp(host: str, port: int, timeout: float = TCP_TIMEOUT) -> bool:
    return tcp_open(host, port, timeout)


def apply_health_success(cam: Camera, *, now: datetime | None = None) -> None:
    cam.fail_count = 0
    cam.last_seen_at = now or datetime.now()
    cam.reachability_status = "ok"


def apply_health_failure(cam: Camera) -> bool:
    """Increment fail_count. Returns True when rediscovery should be attempted."""
    cam.fail_count = int(cam.fail_count or 0) + 1
    if cam.reachability_status != "lost":
        cam.reachability_status = "unreachable"
    return cam.fail_count >= FAIL_THRESHOLD


def _owned_hosts(db: Session, except_camera_id: int) -> set[str]:
    rows = db.query(Camera.host).filter(Camera.id != except_camera_id).all()
    return {h.strip() for (h,) in rows if h and h.strip()}


def first_auth_match(
    hosts: list[str],
    username: str,
    password: str,
    port: int = 554,
    max_workers: int = 8,
) -> str | None:
    """Return the first host that authenticates (as_completed order)."""
    if not hosts:
        return None

    def one(host: str) -> tuple[str, bool]:
        ok, _ = probe_rtsp_auth(host, username, password, port=port)
        return host, ok

    with ThreadPoolExecutor(max_workers=min(max_workers, len(hosts))) as pool:
        futures = [pool.submit(one, h) for h in hosts]
        for fut in as_completed(futures):
            try:
                host, ok = fut.result()
            except Exception:
                logger.debug("auth probe failed", exc_info=True)
                continue
            if ok:
                return host
    return None


def rediscover_camera(
    db: Session,
    cam: Camera,
    *,
    scan_fn: Callable[..., list[str]] | None = None,
    auth_first_fn: Callable[..., str | None] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> str:
    """
    Attempt LAN rediscovery for cam.
    Returns: "updated" | "lost" | "skipped"
    """
    now = now or datetime.now()
    if not force and cam.last_scan_at is not None:
        elapsed = (now - cam.last_scan_at).total_seconds()
        if elapsed < SCAN_COOLDOWN_SEC:
            return "skipped"

    scan = scan_fn or scan_rtsp_hosts
    auth_first = auth_first_fn or first_auth_match

    cam.last_scan_at = now
    try:
        hosts = scan(guess_default_cidr(), port=cam.rtsp_port or 554)
    except ValueError as exc:
        logger.warning("rediscovery scan failed camera_id=%s: %s", cam.id, exc)
        db.commit()
        return "skipped"

    owned = _owned_hosts(db, cam.id)
    # Also skip current host — we already know it's down.
    owned.add((cam.host or "").strip())
    candidates = [h for h in hosts if h.strip() not in owned]

    password = decrypt_secret(cam.password_enc)
    match = auth_first(candidates, cam.username, password, cam.rtsp_port or 554)

    if match:
        cam.host = match
        cam.fail_count = 0
        cam.reachability_status = "ok"
        cam.last_seen_at = now
        db.commit()
        clear_camera_lost(db, cam.id)
        return "updated"

    cam.reachability_status = "lost"
    db.commit()
    ensure_camera_lost(db, cam)
    return "lost"


class CameraMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.enabled = True

    def start_background(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="camera-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if db_module.SessionLocal is None:
                    time.sleep(1)
                    continue
                db = db_module.SessionLocal()
                try:
                    self.tick(db)
                finally:
                    db.close()
            except Exception:
                logger.exception("camera monitor tick failed")
            self._stop.wait(HEALTH_INTERVAL_SEC)

    def tick(self, db: Session) -> None:
        cameras = db.query(Camera).filter(Camera.enabled.is_(True)).all()
        host_updated = False
        for cam in cameras:
            ok = check_camera_tcp(cam.host, cam.rtsp_port or 554)
            if ok:
                was_lost = cam.reachability_status == "lost"
                apply_health_success(cam)
                db.commit()
                if was_lost:
                    clear_camera_lost(db, cam.id)
                continue

            should_scan = apply_health_failure(cam)
            db.commit()
            if not should_scan:
                continue

            result = rediscover_camera(db, cam)
            logger.info("rediscovery camera_id=%s result=%s host=%s", cam.id, result, cam.host)
            if result == "updated":
                host_updated = True

        if host_updated:
            sync_go2rtc(db)
            recorder_manager.reconcile(db)


camera_monitor = CameraMonitor()
