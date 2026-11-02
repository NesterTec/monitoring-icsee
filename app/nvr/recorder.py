from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

import nvr.db as db_module
from nvr.config import get_settings
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


def is_playable_mp4(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    raw = (result.stdout or "").strip().split(",")[0].strip()
    try:
        return float(raw) > 0.5
    except ValueError:
        return False


def remux_ts_to_mp4(ts_path: Path) -> Path | None:
    mp4_path = ts_path.with_suffix(".mp4")
    if mp4_path.exists() and is_playable_mp4(mp4_path):
        return mp4_path
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(ts_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("remux failed %s: %s", ts_path, exc)
        return None
    if result.returncode != 0 or not is_playable_mp4(mp4_path):
        logger.warning("remux invalid output %s: %s", ts_path, result.stderr[-400:])
        mp4_path.unlink(missing_ok=True)
        return None
    try:
        ts_path.unlink(missing_ok=True)
    except OSError:
        pass
    return mp4_path


class RecorderManager:
    def __init__(self) -> None:
        self._procs: dict[int, subprocess.Popen] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._known_files: set[str] = set()
        self._size_seen: dict[str, int] = {}

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

    def stop_camera(self, camera_id: int) -> None:
        proc = self._procs.pop(camera_id, None)
        if proc is not None:
            self._terminate(camera_id, proc)

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
        # MPEG-TS while recording (always valid); remux to MP4 when segment is finished
        pattern = str(out_dir / "%Y-%m-%d" / "%H%M%S.ts")
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
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _file_stable(self, path: Path) -> bool:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        key = str(path)
        prev = self._size_seen.get(key)
        self._size_seen[key] = size
        return prev is not None and prev == size and size > 64 * 1024

    def _scan_new_files(self, db: Session) -> None:
        storage = get_storage_path(db)
        root = storage / "cameras"
        if not root.exists():
            return

        # Finish closed .ts segments → playable .mp4
        for ts_path in root.rglob("*.ts"):
            if not self._file_stable(ts_path):
                continue
            mp4 = remux_ts_to_mp4(ts_path)
            if mp4 is None:
                continue
            self._register_mp4(db, mp4)

        # Legacy / already remuxed mp4
        for path in root.rglob("*.mp4"):
            sp = str(path.resolve())
            if sp in self._known_files:
                continue
            if not self._file_stable(path):
                continue
            if not is_playable_mp4(path):
                logger.info("skipping unplayable mp4 (incomplete moov): %s", sp)
                continue
            self._register_mp4(db, path)

        self._cleanup_broken_db_rows(db)

    def _register_mp4(self, db: Session, path: Path) -> None:
        sp = str(path.resolve())
        if sp in self._known_files:
            return
        try:
            cam_id = int(path.parent.parent.name)
            size = path.stat().st_size
        except (ValueError, OSError):
            return
        exists = db.query(RecordingSegment).filter(RecordingSegment.path == sp).one_or_none()
        if exists:
            exists.size_bytes = size
            db.commit()
            self._known_files.add(sp)
            return
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
        logger.info("registered segment camera_id=%s path=%s", cam_id, sp)

    def _cleanup_broken_db_rows(self, db: Session) -> None:
        for seg in db.query(RecordingSegment).order_by(RecordingSegment.id.desc()).limit(100).all():
            path = Path(seg.path)
            if not path.exists():
                db.delete(seg)
                db.commit()
                continue
            if path.suffix.lower() == ".mp4" and not is_playable_mp4(path):
                # keep if still growing (active write of old format)
                if not self._file_stable(path):
                    continue
                logger.info("removing broken segment %s", seg.path)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                db.delete(seg)
                db.commit()

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
            self._stop.wait(20)


recorder_manager = RecorderManager()
