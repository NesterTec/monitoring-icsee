from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.models import Camera
from nvr.rtsp import build_rtsp_url
from nvr.security import decrypt_secret


def sync_go2rtc(db: Session) -> Path:
    settings = get_settings()
    path = Path(settings.go2rtc_config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    streams: dict = {}
    cameras = db.query(Camera).filter(Camera.enabled.is_(True)).all()
    for cam in cameras:
        password = decrypt_secret(cam.password_enc)
        sub = build_rtsp_url(cam.host, cam.rtsp_port, cam.username, password, cam.channel, cam.stream_sub)
        main = build_rtsp_url(cam.host, cam.rtsp_port, cam.username, password, cam.channel, cam.stream_main)
        name = f"cam_{cam.id}"
        streams[name] = [sub, f"ffmpeg:{name}#video=h264"]
        streams[f"{name}_hd"] = [main, f"ffmpeg:{name}_hd#video=h264"]

    data = {
        "api": {"listen": ":1984"},
        "rtsp": {"listen": ":8554"},
        "webrtc": {"listen": ":8555"},
        "log": {"level": "info"},
        "streams": streams,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
