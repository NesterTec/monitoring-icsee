from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nvr.auth import authenticate, require_user, seed_admin
from nvr.config import get_settings
from nvr.db import get_db
from nvr.go2rtc_sync import sync_go2rtc
from nvr.models import Camera, RecordingSegment, User
from nvr.quota import dir_size, free_bytes
from nvr.recorder import recorder_manager
from nvr.rtsp import build_rtsp_url, probe_rtsp
from nvr.security import decrypt_secret, encrypt_secret
from nvr.settings_service import get_quota_gb, get_storage_path, set_quota_gb, set_storage_path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter()


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
    cameras = db.query(Camera).filter(Camera.enabled.is_(True)).order_by(Camera.name).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "cameras": cameras, "go2rtc_public": get_settings().go2rtc_public_url},
    )


@router.get("/cameras", response_class=HTMLResponse)
def cameras_list(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = db.query(Camera).order_by(Camera.name).all()
    return templates.TemplateResponse(request, "cameras.html", {"user": user, "cameras": cameras})


@router.get("/cameras/new", response_class=HTMLResponse)
def cameras_new(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "camera_form.html", {"user": user, "camera": None, "error": None})


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
    return templates.TemplateResponse(request, "camera_form.html", {"user": user, "camera": cam, "error": None})


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
    db.commit()
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    return RedirectResponse("/cameras", status_code=303)


@router.post("/cameras/{camera_id}/delete")
def cameras_delete(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = db.get(Camera, camera_id)
    if cam:
        db.delete(cam)
        db.commit()
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


@router.get("/recordings", response_class=HTMLResponse)
def recordings_list(
    request: Request,
    camera_id: int | None = None,
    date: str | None = None,
    db: Session = Depends(get_db),
):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    q = db.query(RecordingSegment).order_by(RecordingSegment.started_at.desc())
    if camera_id:
        q = q.filter(RecordingSegment.camera_id == camera_id)
    if date:
        day = datetime.strptime(date, "%Y-%m-%d")
        q = q.filter(RecordingSegment.started_at >= day)
    segments = q.limit(200).all()
    cameras = db.query(Camera).order_by(Camera.name).all()
    return templates.TemplateResponse(
        request,
        "recordings.html",
        {"user": user, "segments": segments, "cameras": cameras, "camera_id": camera_id, "date": date or ""},
    )


@router.get("/recordings/{segment_id}/file")
def recordings_file(segment_id: int, request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    seg = db.get(RecordingSegment, segment_id)
    if seg is None or not Path(seg.path).is_file():
        raise HTTPException(404, "segment not found")
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
