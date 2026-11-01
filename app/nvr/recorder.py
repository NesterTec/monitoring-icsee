from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from nvr.config import get_settings
import nvr.db as db_module
from nvr.models import Camera, RecordingSegment
from nvr.quota import enforce_quota
from nvr.rtsp import build_rtsp_url
from nvr.security import decrypt_secret
from nvr.settings_service import get_quota_gb, get_storage_path

logger = logging.getLogger(__name__)


def segment_dir(storage: Path, camera_id: int) -> Path:
    path = storage / "cameras" / str(camera_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


class RecorderManager:
    def __init__(self) -> None:
        self._procs: dict[int, subprocess.Popen] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._known_files: set[str] = set()

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="recorder-loop", daemon=True)
        self._thread.start()

    def stop_all(self) -> None:
        self._stop.set()
        for cam_id, proc in list(self._procs.items()):
            self._terminate(cam_id, proc)
        self._procs.clear()

    def reconcile(self, db: Session) -> None:
        storage = get_storage_path(db)
        wanted = {
            c.id: c
            for c in db.query(Camera).filter(Camera.enabled.is_(True), Camera.recording_enabled.is_(True)).all()
        }
        for cam_id in list(self._procs):
            if cam_id not in wanted:
                self._terminate(cam_id, self._procs.pop(cam_id))
        for cam_id, cam in wanted.items():
            proc = self._procs.get(cam_id)
            if proc is None or proc.poll() is not None:
                self._procs[cam_id] = self._spawn(cam, storage)

    def _spawn(self, cam: Camera, storage: Path) -> subprocess.Popen:
        password = decrypt_secret(cam.password_enc)
        url = build_rtsp_url(cam.host, cam.rtsp_port, cam.username, password, cam.channel, cam.stream_main)
        out_dir = segment_dir(storage, cam.id)
        pattern = str(out_dir / "%Y-%m-%d" / "%H%M%S.mp4")
        (out_dir / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)
        settings = get_settings()
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-f",
            "segment",
            "-segment_time",
            str(settings.segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
        ]
        logger.info("starting recorder camera_id=%s", cam.id)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _terminate(self, cam_id: int, proc: subprocess.Popen) -> None:
        logger.info("stopping recorder camera_id=%s", cam_id)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _scan_new_files(self, db: Session) -> None:
        storage = get_storage_path(db)
        root = storage / "cameras"
        if not root.exists():
            return
        for path in root.rglob("*.mp4"):
            sp = str(path.resolve())
            if sp in self._known_files:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < 1024:
                continue
            # cameras/{id}/date/file.mp4
            try:
                cam_id = int(path.parent.parent.name)
            except ValueError:
                continue
            exists = db.query(RecordingSegment).filter(RecordingSegment.path == sp).one_or_none()
            if exists:
                self._known_files.add(sp)
                continue
            started = datetime.fromtimestamp(path.stat().st_mtime)
            db.add(
                RecordingSegment(
                    camera_id=cam_id,
                    path=sp,
                    started_at=started,
                    ended_at=None,
                    size_bytes=size,
                )
            )
            db.commit()
            self._known_files.add(sp)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if db_module.SessionLocal is None:
                time.sleep(1)
                continue
            db = db_module.SessionLocal()
            try:
                self.reconcile(db)
                self._scan_new_files(db)
                settings = get_settings()
                enforce_quota(db, get_storage_path(db), get_quota_gb(db), settings.disk_reserve_bytes)
            except Exception:
                logger.exception("recorder loop error")
            finally:
                db.close()
            self._stop.wait(30)


recorder_manager = RecorderManager()
