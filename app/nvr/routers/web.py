import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nvr.auth import authenticate, require_user, seed_admin
from nvr.config import get_settings
from nvr.db import get_db
from nvr.discover import auth_probe_hosts, guess_default_cidr, scan_rtsp_hosts
from nvr.go2rtc_sync import sync_go2rtc
from nvr.models import Camera, RecordingSegment, User
from nvr.quota import dir_size, free_bytes
from nvr.recorder import recorder_manager, segment_dir
from nvr.rtsp import build_rtsp_url, probe_rtsp
from nvr.security import decrypt_secret, encrypt_secret
from nvr.settings_service import get_quota_gb, get_storage_path, set_quota_gb, set_storage_path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter()


def _cameras_ordered(db: Session, *, only_live: bool = False) -> list[Camera]:
    q = db.query(Camera)
    if only_live:
        q = q.filter(Camera.enabled.is_(True), Camera.dashboard_visible.is_(True))
    return q.order_by(Camera.sort_order.asc(), Camera.id.asc()).all()


def _next_sort_order(db: Session) -> int:
    current = db.query(Camera).order_by(Camera.sort_order.desc(), Camera.id.desc()).first()
    return (current.sort_order + 1) if current is not None else 0


def _user_or_redirect(request: Request, db: Session) -> User | RedirectResponse:
    try:
        return require_user(request, db)
    except HTTPException:
        return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    seed_admin(db)
    user = authenticate(db, email.strip(), password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "E-mail ou senha inválidos"},
            status_code=401,
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = _cameras_ordered(db, only_live=True)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "cameras": cameras, "go2rtc_public": get_settings().go2rtc_public_url},
    )


@router.get("/live/{camera_id}/fullscreen", response_class=HTMLResponse)
def live_fullscreen(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam is None or not cam.enabled:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "live_fullscreen.html",
        {"user": user, "camera": cam, "go2rtc_public": get_settings().go2rtc_public_url},
    )


@router.get("/cameras", response_class=HTMLResponse)
def cameras_list(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = _cameras_ordered(db)
    return templates.TemplateResponse(request, "cameras.html", {"user": user, "cameras": cameras})


@router.get("/cameras/new", response_class=HTMLResponse)
def cameras_new(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "camera_form.html",
        {
            "user": user,
            "camera": None,
            "error": None,
            "default_cidr": guess_default_cidr(),
        },
    )


@router.post("/cameras/discover/scan")
async def cameras_discover_scan(request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    payload = await request.json()
    cidr = str(payload.get("cidr") or guess_default_cidr()).strip()
    port = int(payload.get("port") or 554)
    try:
        hosts = scan_rtsp_hosts(cidr, port=port)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    existing = {c.host for c in db.query(Camera).all()}
    return {
        "cidr": cidr,
        "port": port,
        "hosts": [
            {"host": h, "registered": h in existing}
            for h in hosts
        ],
    }


@router.post("/cameras/discover/auth")
async def cameras_discover_auth(request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    payload = await request.json()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    port = int(payload.get("port") or 554)
    hosts = [str(h).strip() for h in (payload.get("hosts") or []) if str(h).strip()]
    if not username or not password:
        raise HTTPException(400, detail="Informe usuário e senha do dispositivo")
    if not hosts:
        raise HTTPException(400, detail="Nenhum IP para testar — rode a varredura antes")
    if len(hosts) > 64:
        raise HTTPException(400, detail="Muitos hosts (máx. 64)")
    results = auth_probe_hosts(hosts, username, password, port=port)
    matches = [r for r in results if r["ok"]]
    return {"matches": matches, "tried": len(results), "results": results}


@router.post("/cameras/create")
async def cameras_create(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    password = str(form.get("password") or "")
    cam = Camera(
        name=str(form.get("name") or "").strip(),
        host=str(form.get("host") or "").strip(),
        username=str(form.get("username") or "").strip(),
        password_enc=encrypt_secret(password),
        rtsp_port=int(form.get("rtsp_port") or 554),
        channel=int(form.get("channel") or 1),
        stream_sub=int(form.get("stream_sub") or 1),
        stream_main=int(form.get("stream_main") or 0),
        recording_enabled=form.get("recording_enabled") == "on",
        enabled=form.get("enabled") == "on",
        dashboard_visible=form.get("dashboard_visible") == "on",
        sort_order=_next_sort_order(db),
    )
    db.add(cam)
    db.commit()
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    return RedirectResponse("/cameras", status_code=303)


@router.get("/cameras/{camera_id}/edit", response_class=HTMLResponse)
def cameras_edit(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "camera_form.html",
        {"user": user, "camera": cam, "error": None, "default_cidr": guess_default_cidr()},
    )


@router.post("/cameras/{camera_id}")
async def cameras_update(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404)
    form = await request.form()
    cam.name = str(form.get("name", cam.name)).strip()
    cam.host = str(form.get("host", cam.host)).strip()
    cam.username = str(form.get("username", cam.username)).strip()
    password = str(form.get("password") or "").strip()
    if password:
        cam.password_enc = encrypt_secret(password)
    cam.rtsp_port = int(form.get("rtsp_port") or cam.rtsp_port)
    cam.channel = int(form.get("channel") or cam.channel)
    cam.stream_sub = int(form.get("stream_sub") or cam.stream_sub)
    cam.stream_main = int(form.get("stream_main") or cam.stream_main)
    cam.recording_enabled = form.get("recording_enabled") == "on"
    cam.enabled = form.get("enabled") == "on"
    cam.dashboard_visible = form.get("dashboard_visible") == "on"
    db.commit()
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    return RedirectResponse("/cameras", status_code=303)


@router.post("/cameras/{camera_id}/toggle-dashboard")
def cameras_toggle_dashboard(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404)
    cam.dashboard_visible = not bool(cam.dashboard_visible)
    db.commit()
    return RedirectResponse("/cameras", status_code=303)


@router.post("/cameras/{camera_id}/move")
async def cameras_move(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404)
    form = await request.form()
    direction = str(form.get("direction") or "").strip().lower()
    cameras = _cameras_ordered(db)
    idx = next((i for i, c in enumerate(cameras) if c.id == cam.id), None)
    if idx is None:
        return RedirectResponse("/cameras", status_code=303)
    if direction == "up" and idx > 0:
        other = cameras[idx - 1]
    elif direction == "down" and idx < len(cameras) - 1:
        other = cameras[idx + 1]
    else:
        return RedirectResponse("/cameras", status_code=303)
    cam.sort_order, other.sort_order = other.sort_order, cam.sort_order
    db.commit()
    return RedirectResponse("/cameras", status_code=303)


@router.post("/cameras/{camera_id}/delete")
def cameras_delete(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam:
        storage = get_storage_path(db)
        cam_dir = segment_dir(storage, cam.id)
        # Stop ffmpeg first so files are not locked / re-registered.
        recorder_manager.stop_camera(cam.id)
        db.query(RecordingSegment).filter(RecordingSegment.camera_id == cam.id).delete()
        db.delete(cam)
        db.commit()
        shutil.rmtree(cam_dir, ignore_errors=True)
        sync_go2rtc(db)
        recorder_manager.reconcile(db)
    return RedirectResponse("/cameras", status_code=303)


@router.post("/cameras/{camera_id}/test")
def cameras_test(camera_id: int, request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404)
    password = decrypt_secret(cam.password_enc)
    url = build_rtsp_url(cam.host, cam.rtsp_port, cam.username, password, cam.channel, cam.stream_sub)
    ok, detail = probe_rtsp(url)
    return {"ok": ok, "detail": detail}


def _recording_presets():
    from datetime import timedelta

    return {
        "30m": ("Últimos 30 min", timedelta(minutes=30)),
        "1h": ("Última hora", timedelta(hours=1)),
        "6h": ("Últimas 6 horas", timedelta(hours=6)),
        "24h": ("Últimas 24 horas", timedelta(hours=24)),
        "7d": ("Últimos 7 dias", timedelta(days=7)),
        "30d": ("Último mês", timedelta(days=30)),
    }


@router.get("/recordings", response_class=HTMLResponse)
def recordings_list(
    request: Request,
    camera_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    preset: str | None = None,
    db: Session = Depends(get_db),
):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = _cameras_ordered(db)
    presets = _recording_presets()
    segments: list[RecordingSegment] = []
    error = None
    range_label = None
    use_preset = bool(preset and preset in presets)
    use_custom = bool(date_from and date_to)
    searched = bool(camera_id and (use_preset or use_custom))

    if searched:
        try:
            now = datetime.now()
            if use_preset:
                _label, delta = presets[preset]
                start = now - delta
                end = now
                range_label = _label
            else:
                start = datetime.strptime(date_from, "%Y-%m-%d")
                end = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                if end < start:
                    raise ValueError("A data final deve ser maior ou igual à data inicial.")
                range_label = f"{date_from} → {date_to}"
            segments = (
                db.query(RecordingSegment)
                .filter(
                    RecordingSegment.camera_id == camera_id,
                    RecordingSegment.started_at >= start,
                    RecordingSegment.started_at <= end,
                )
                .order_by(RecordingSegment.started_at.desc())
                .limit(300)
                .all()
            )
        except ValueError as exc:
            error = str(exc)
            searched = False
    elif camera_id and not use_preset and not use_custom:
        error = "Escolha um período rápido ou informe as datas De/Até."

    return templates.TemplateResponse(
        request,
        "recordings.html",
        {
            "user": user,
            "segments": segments,
            "cameras": cameras,
            "camera_id": camera_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "preset": preset or "",
            "presets": presets,
            "searched": searched,
            "range_label": range_label,
            "error": error,
        },
    )


@router.get("/recordings/{segment_id}/file")
def recordings_file(segment_id: int, request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    seg = db.get(RecordingSegment, segment_id)
    if seg is None or not Path(seg.path).is_file():
        raise HTTPException(404, "segment not found")
    from nvr.recorder import is_playable_mp4

    if not is_playable_mp4(Path(seg.path)):
        raise HTTPException(
            409,
            "Arquivo incompleto (gravação interrompida). Esse segmento será removido automaticamente.",
        )
    return FileResponse(seg.path, media_type="video/mp4", filename=Path(seg.path).name)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    storage = get_storage_path(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "storage_path": str(storage),
            "quota_gb": get_quota_gb(db),
            "used_bytes": dir_size(storage),
            "free_bytes": free_bytes(storage),
            "error": None,
            "ok": None,
        },
    )


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    storage_path = str(form.get("storage_path") or "").strip()
    quota_gb = float(form.get("quota_gb") or 0)
    error = None
    ok = None
    try:
        storage = set_storage_path(db, storage_path)
        free = free_bytes(storage)
        used = dir_size(storage)
        if int(quota_gb * 1024**3) > used + free:
            raise ValueError("Cota maior que o espaço disponível no disco")
        set_quota_gb(db, quota_gb)
        recorder_manager.reconcile(db)
        ok = "Configurações salvas"
    except Exception as exc:
        error = str(exc)
    storage = get_storage_path(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "storage_path": str(storage),
            "quota_gb": get_quota_gb(db),
            "used_bytes": dir_size(storage),
            "free_bytes": free_bytes(storage),
            "error": error,
            "ok": ok,
        },
    )
