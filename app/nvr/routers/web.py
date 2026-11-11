import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nvr.auth import (
    authenticate,
    camera_for_user,
    create_user_with_site,
    is_superadmin,
    primary_site_for_user,
    require_superadmin,
    require_user,
    seed_admin,
    user_site_ids,
    user_sites,
)
from nvr.config import get_settings
from nvr.db import get_db
from nvr.camera_monitor import rediscover_camera
from nvr.discover import auth_probe_hosts, guess_default_cidr, probe_rtsp_auth, scan_rtsp_hosts
from nvr.go2rtc_sync import sync_go2rtc
from nvr.ipmap import lan_to_vpn, vpn_to_lan
from nvr.models import Camera, RecordingSegment, Site, User
from nvr.health_logs import list_recent
from nvr.quota import dir_size, free_bytes
from nvr.reattach import reattach_from_scanned_hosts
from nvr.recorder import recorder_manager, segment_dir
from nvr.rtsp import build_rtsp_url, probe_rtsp
from nvr.security import decrypt_secret, encrypt_secret, hash_password
from nvr.settings_service import get_quota_gb, get_storage_path, set_quota_gb, set_storage_path
from nvr.urls import abs_path, public_base_path
from nvr.wireguard_provision import (
    apply_camera_host_mapping,
    client_conf_text,
    provision_site_keys,
    server_peer_snippet,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(abs_path(path), status_code=303)


def _tpl(request: Request, name: str, context: dict, **kwargs):
    ctx = {"base_path": public_base_path(), **context}
    if "user" in ctx and ctx["user"] is not None:
        ctx.setdefault("is_superadmin", is_superadmin(ctx["user"]))
    return templates.TemplateResponse(request, name, ctx, **kwargs)


def _cameras_ordered(
    db: Session,
    user: User,
    *,
    only_live: bool = False,
) -> list[Camera]:
    q = db.query(Camera)
    if not is_superadmin(user):
        ids = user_site_ids(db, user)
        if not ids:
            return []
        q = q.filter(Camera.site_id.in_(ids))
    if only_live:
        q = q.filter(Camera.enabled.is_(True), Camera.dashboard_visible.is_(True))
    return q.order_by(Camera.sort_order.asc(), Camera.id.asc()).all()


def _next_sort_order(db: Session, site_id: int | None) -> int:
    q = db.query(Camera)
    if site_id is not None:
        q = q.filter(Camera.site_id == site_id)
    current = q.order_by(Camera.sort_order.desc(), Camera.id.desc()).first()
    return (current.sort_order + 1) if current is not None else 0


def _user_or_redirect(request: Request, db: Session) -> User | RedirectResponse:
    try:
        return require_user(request, db)
    except HTTPException:
        return _redirect("/login")


def _site_default_cidr(db: Session, user: User) -> str:
    site = primary_site_for_user(db, user)
    if site and site.vpn_cidr:
        return site.vpn_cidr
    return guess_default_cidr()


def _resolve_hosts_for_site(site: Site, lan_or_host: str) -> tuple[str, str]:
    """Return (host_lan, host). Maps LAN→VPN when possible; accepts VPN IP; else passthrough."""
    value = lan_or_host.strip()
    try:
        return value, lan_to_vpn(value, site.lan_cidr, site.vpn_cidr)
    except ValueError:
        pass
    try:
        return vpn_to_lan(value, site.lan_cidr, site.vpn_cidr), value
    except ValueError:
        return value, value


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _tpl(request, "login.html", {"error": None})


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
        return _tpl(
            request,
            "login.html",
            {"error": "E-mail ou senha inválidos"},
            status_code=401,
        )
    request.session["user_id"] = user.id
    return _redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = _cameras_ordered(db, user, only_live=True)
    return _tpl(
        request,
        "dashboard.html",
        {"user": user, "cameras": cameras, "go2rtc_public": get_settings().go2rtc_public_url},
    )


@router.get("/live/{camera_id}/fullscreen", response_class=HTMLResponse)
def live_fullscreen(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None or not cam.enabled:
        raise HTTPException(404)
    return _tpl(
        request,
        "live_fullscreen.html",
        {"user": user, "camera": cam, "go2rtc_public": get_settings().go2rtc_public_url},
    )


@router.get("/cameras", response_class=HTMLResponse)
def cameras_list(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cameras = _cameras_ordered(db, user)
    return _tpl(request, "cameras.html", {"user": user, "cameras": cameras})


@router.get("/cameras/new", response_class=HTMLResponse)
def cameras_new(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    site = primary_site_for_user(db, user)
    return _tpl(
        request,
        "camera_form.html",
        {
            "user": user,
            "camera": None,
            "error": None,
            "default_cidr": _site_default_cidr(db, user),
            "site": site,
        },
    )


@router.post("/cameras/discover/scan")
async def cameras_discover_scan(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    payload = await request.json()
    site = primary_site_for_user(db, user)
    default = site.vpn_cidr if site else guess_default_cidr()
    cidr = str(payload.get("cidr") or default).strip()
    port = int(payload.get("port") or 554)
    try:
        hosts = scan_rtsp_hosts(cidr, port=port)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    site_ids = user_site_ids(db, user)
    q = db.query(Camera)
    if not is_superadmin(user):
        q = q.filter(Camera.site_id.in_(site_ids)) if site_ids else q.filter(False)
    existing = {c.host for c in q.all()}
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


@router.post("/cameras/discover/reattach")
async def cameras_discover_reattach(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    payload = await request.json()
    port = int(payload.get("port") or 554)
    hosts = [str(h).strip() for h in (payload.get("hosts") or []) if str(h).strip()]
    if not hosts:
        raise HTTPException(400, detail="Nenhum IP para testar — rode a varredura antes")
    if len(hosts) > 64:
        raise HTTPException(400, detail="Muitos hosts (máx. 64)")
    site_ids = user_site_ids(db, user)
    if not site_ids and not is_superadmin(user):
        raise HTTPException(400, detail="Nenhuma localidade associada")
    scoped = None if is_superadmin(user) else site_ids
    cams_q = db.query(Camera)
    if scoped is not None:
        cams_q = cams_q.filter(Camera.site_id.in_(scoped))
    if cams_q.count() == 0:
        raise HTTPException(400, detail="Nenhuma câmera cadastrada para reatachar")
    result = reattach_from_scanned_hosts(db, hosts, port=port, site_ids=scoped)
    if result["updated"]:
        sync_go2rtc(db)
        recorder_manager.reconcile(db)
    return result


@router.post("/cameras/create")
async def cameras_create(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    site = primary_site_for_user(db, user)
    if site is None:
        return _tpl(
            request,
            "camera_form.html",
            {
                "user": user,
                "camera": None,
                "error": "Nenhuma localidade disponível. Peça ao superadmin para criar um site.",
                "default_cidr": guess_default_cidr(),
                "site": None,
            },
            status_code=400,
        )
    form = await request.form()
    password = str(form.get("password") or "")
    lan_or_host = str(form.get("host_lan") or form.get("host") or "").strip()
    if not lan_or_host:
        return _tpl(
            request,
            "camera_form.html",
            {
                "user": user,
                "camera": None,
                "error": "Informe o IP da câmera na LAN",
                "default_cidr": site.vpn_cidr,
                "site": site,
            },
            status_code=400,
        )
    host_lan, host = _resolve_hosts_for_site(site, lan_or_host)
    cam = Camera(
        site_id=site.id,
        name=str(form.get("name") or "").strip(),
        host=host,
        host_lan=host_lan,
        username=str(form.get("username") or "").strip(),
        password_enc=encrypt_secret(password),
        rtsp_port=int(form.get("rtsp_port") or 554),
        channel=int(form.get("channel") or 1),
        stream_sub=int(form.get("stream_sub") or 1),
        stream_main=int(form.get("stream_main") or 0),
        recording_enabled=form.get("recording_enabled") == "on",
        enabled=form.get("enabled") == "on",
        dashboard_visible=form.get("dashboard_visible") == "on",
        sort_order=_next_sort_order(db, site.id),
    )
    db.add(cam)
    db.commit()
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    return _redirect("/cameras")


@router.get("/cameras/{camera_id}/edit", response_class=HTMLResponse)
def cameras_edit(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    site = db.get(Site, cam.site_id) if cam.site_id else primary_site_for_user(db, user)
    return _tpl(
        request,
        "camera_form.html",
        {
            "user": user,
            "camera": cam,
            "error": None,
            "default_cidr": site.vpn_cidr if site else guess_default_cidr(),
            "site": site,
        },
    )


@router.post("/cameras/{camera_id}")
async def cameras_update(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    site = db.get(Site, cam.site_id) if cam.site_id else primary_site_for_user(db, user)
    form = await request.form()
    cam.name = str(form.get("name", cam.name)).strip()
    lan_or_host = str(form.get("host_lan") or form.get("host") or cam.host_lan or cam.host).strip()
    if site:
        cam.host_lan, cam.host = _resolve_hosts_for_site(site, lan_or_host)
    else:
        cam.host = lan_or_host
        cam.host_lan = lan_or_host
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
    return _redirect("/cameras")


@router.post("/cameras/{camera_id}/toggle-dashboard")
def cameras_toggle_dashboard(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    cam.dashboard_visible = not bool(cam.dashboard_visible)
    db.commit()
    return _redirect("/cameras")


@router.post("/cameras/{camera_id}/move")
async def cameras_move(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    form = await request.form()
    direction = str(form.get("direction") or "").strip().lower()
    cameras = _cameras_ordered(db, user)
    idx = next((i for i, c in enumerate(cameras) if c.id == cam.id), None)
    if idx is None:
        return _redirect("/cameras")
    if direction == "up" and idx > 0:
        other = cameras[idx - 1]
    elif direction == "down" and idx < len(cameras) - 1:
        other = cameras[idx + 1]
    else:
        return _redirect("/cameras")
    cam.sort_order, other.sort_order = other.sort_order, cam.sort_order
    db.commit()
    return _redirect("/cameras")


@router.post("/cameras/{camera_id}/delete")
def cameras_delete(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam:
        storage = get_storage_path(db)
        cam_dir = segment_dir(storage, cam.id)
        recorder_manager.stop_camera(cam.id)
        db.query(RecordingSegment).filter(RecordingSegment.camera_id == cam.id).delete()
        db.delete(cam)
        db.commit()
        shutil.rmtree(cam_dir, ignore_errors=True)
        sync_go2rtc(db)
        recorder_manager.reconcile(db)
    return _redirect("/cameras")


@router.post("/cameras/{camera_id}/test")
def cameras_test(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    password = decrypt_secret(cam.password_enc)
    url = build_rtsp_url(cam.host, cam.rtsp_port, cam.username, password, cam.channel, cam.stream_sub)
    ok, detail = probe_rtsp(url)
    return {"ok": ok, "detail": detail}


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if is_superadmin(user):
        logs = list_recent(db, limit=30)
    else:
        cam_ids = [c.id for c in _cameras_ordered(db, user)]
        logs = list_recent(db, limit=30, camera_ids=cam_ids)
    return _tpl(request, "logs.html", {"user": user, "logs": logs})


@router.post("/cameras/{camera_id}/retry-reachability")
def cameras_retry_reachability(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    result = rediscover_camera(db, cam, force=True)
    if result == "updated":
        sync_go2rtc(db)
        recorder_manager.reconcile(db)
    db.refresh(cam)
    return {
        "ok": result == "updated",
        "result": result,
        "host": cam.host,
        "host_lan": cam.host_lan,
        "reachability_status": cam.reachability_status,
    }


@router.post("/cameras/{camera_id}/replace")
async def cameras_replace(camera_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cam = camera_for_user(db, user, camera_id)
    if cam is None:
        raise HTTPException(404)
    site = db.get(Site, cam.site_id) if cam.site_id else primary_site_for_user(db, user)
    form = await request.form()
    lan_or_host = str(form.get("host") or form.get("host_lan") or "").strip()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    rtsp_port = int(form.get("rtsp_port") or cam.rtsp_port or 554)
    channel = int(form.get("channel") or cam.channel or 1)
    stream_sub = int(form.get("stream_sub") or cam.stream_sub or 1)
    if not lan_or_host or not username or not password:
        return _tpl(
            request,
            "camera_form.html",
            {
                "user": user,
                "camera": cam,
                "error": "Informe host, usuário e senha da nova câmera.",
                "default_cidr": site.vpn_cidr if site else guess_default_cidr(),
                "site": site,
            },
            status_code=400,
        )
    if site:
        host_lan, host = _resolve_hosts_for_site(site, lan_or_host)
    else:
        host_lan, host = lan_or_host, lan_or_host
    ok, detail = probe_rtsp_auth(host, username, password, port=rtsp_port, channel=channel, stream=stream_sub)
    if not ok:
        return _tpl(
            request,
            "camera_form.html",
            {
                "user": user,
                "camera": cam,
                "error": f"Falha ao autenticar no novo dispositivo: {detail}",
                "default_cidr": site.vpn_cidr if site else guess_default_cidr(),
                "site": site,
            },
            status_code=400,
        )
    cam.host = host
    cam.host_lan = host_lan
    cam.username = username
    cam.password_enc = encrypt_secret(password)
    cam.rtsp_port = rtsp_port
    cam.channel = channel
    cam.stream_sub = stream_sub
    if form.get("stream_main") is not None and str(form.get("stream_main")).strip() != "":
        cam.stream_main = int(form.get("stream_main"))
    cam.fail_count = 0
    cam.reachability_status = "ok"
    cam.last_seen_at = datetime.now()
    db.commit()
    from nvr.notifications import clear_camera_lost

    clear_camera_lost(db, cam.id)
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    return _redirect("/cameras")


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
    cameras = _cameras_ordered(db, user)
    allowed_ids = {c.id for c in cameras}
    if camera_id is not None and camera_id not in allowed_ids:
        raise HTTPException(404)
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

    return _tpl(
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
    user = require_user(request, db)
    seg = db.get(RecordingSegment, segment_id)
    if seg is None or not Path(seg.path).is_file():
        raise HTTPException(404, "segment not found")
    if camera_for_user(db, user, seg.camera_id) is None:
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
    return _tpl(
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
    if not is_superadmin(user):
        raise HTTPException(403, detail="Apenas superadmin altera armazenamento global")
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
    return _tpl(
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


# --- Site (localidade) for regular users ---


@router.get("/site", response_class=HTMLResponse)
def site_page(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    sites = user_sites(db, user)
    if is_superadmin(user):
        sites = [s for s in sites if s.owner_user_id == user.id] or sites[:1]
    site = sites[0] if sites else None
    settings = get_settings()
    return _tpl(
        request,
        "site.html",
        {
            "user": user,
            "site": site,
            "wg_ready": bool(settings.wg_server_public_key and settings.wg_server_endpoint),
            "error": None,
            "ok": None,
        },
    )


@router.post("/site")
async def site_update(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    site = primary_site_for_user(db, user)
    if site is None or (not is_superadmin(user) and site.owner_user_id != user.id):
        raise HTTPException(404)
    form = await request.form()
    site.lan_iface = str(form.get("lan_iface") or site.lan_iface).strip() or site.lan_iface
    lan_cidr = str(form.get("lan_cidr") or site.lan_cidr).strip()
    if lan_cidr:
        site.lan_cidr = lan_cidr
    db.commit()
    return _tpl(
        request,
        "site.html",
        {
            "user": user,
            "site": site,
            "wg_ready": bool(get_settings().wg_server_public_key and get_settings().wg_server_endpoint),
            "error": None,
            "ok": "Localidade atualizada",
        },
    )


@router.get("/site/wireguard.conf")
def site_download_conf(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    site = primary_site_for_user(db, user)
    if site is None or (not is_superadmin(user) and site.owner_user_id != user.id):
        raise HTTPException(404)
    provision_site_keys(db, site)
    db.refresh(site)
    try:
        body = client_conf_text(site)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    apply_camera_host_mapping(db, site)
    sync_go2rtc(db)
    recorder_manager.reconcile(db)
    filename = f"icsee-site-{site.id}-wg0.conf"
    return PlainTextResponse(
        body,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Superadmin ---


@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db)):
    user = _user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not is_superadmin(user):
        raise HTTPException(403)
    users = db.query(User).order_by(User.id.asc()).all()
    sites = db.query(Site).order_by(Site.id.asc()).all()
    return _tpl(
        request,
        "admin.html",
        {
            "user": user,
            "users": users,
            "sites": sites,
            "error": None,
            "ok": None,
            "wg_ready": bool(get_settings().wg_server_public_key and get_settings().wg_server_endpoint),
        },
    )


@router.post("/admin/users/create")
async def admin_create_user(request: Request, db: Session = Depends(get_db)):
    admin = require_superadmin(request, db)
    form = await request.form()
    email = str(form.get("email") or "").strip()
    password = str(form.get("password") or "")
    site_name = str(form.get("site_name") or "").strip()
    lan_cidr = str(form.get("lan_cidr") or "192.168.1.0/24").strip()
    lan_iface = str(form.get("lan_iface") or "eth0").strip()
    error = None
    ok = None
    try:
        if not email or not password:
            raise ValueError("Informe e-mail e senha")
        new_user, site = create_user_with_site(
            db,
            email=email,
            password=password,
            site_name=site_name or f"Site {email}",
            lan_cidr=lan_cidr,
            lan_iface=lan_iface,
        )
        provision_site_keys(db, site)
        ok = f"Usuário {new_user.email} e site #{site.id} criados (VPN {site.vpn_cidr})"
    except Exception as exc:
        error = str(exc)
    users = db.query(User).order_by(User.id.asc()).all()
    sites = db.query(Site).order_by(Site.id.asc()).all()
    return _tpl(
        request,
        "admin.html",
        {
            "user": admin,
            "users": users,
            "sites": sites,
            "error": error,
            "ok": ok,
            "wg_ready": bool(get_settings().wg_server_public_key and get_settings().wg_server_endpoint),
        },
        status_code=400 if error else 200,
    )


@router.post("/admin/sites/{site_id}/provision")
def admin_provision_site(site_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_superadmin(request, db)
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404)
    provision_site_keys(db, site, rotate=True)
    apply_camera_host_mapping(db, site)
    sync_go2rtc(db)
    return _redirect("/admin")


@router.get("/admin/sites/{site_id}/server-peer.conf")
def admin_server_peer(site_id: int, request: Request, db: Session = Depends(get_db)):
    require_superadmin(request, db)
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404)
    if not site.wg_public_key:
        provision_site_keys(db, site)
        db.refresh(site)
    body = server_peer_snippet(site)
    return PlainTextResponse(
        body,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="site-{site.id}-server-peer.conf"'},
    )


@router.get("/admin/sites/{site_id}/client.conf")
def admin_client_conf(site_id: int, request: Request, db: Session = Depends(get_db)):
    require_superadmin(request, db)
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404)
    provision_site_keys(db, site)
    db.refresh(site)
    try:
        body = client_conf_text(site)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return PlainTextResponse(
        body,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="icsee-site-{site.id}-wg0.conf"'},
    )


@router.post("/admin/users/{user_id}/password")
async def admin_reset_password(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_superadmin(request, db)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    form = await request.form()
    password = str(form.get("password") or "")
    error = None
    ok = None
    if len(password) < 6:
        error = "Senha deve ter ao menos 6 caracteres"
    else:
        target.password_hash = hash_password(password)
        db.commit()
        ok = f"Senha de {target.email} atualizada"
    users = db.query(User).order_by(User.id.asc()).all()
    sites = db.query(Site).order_by(Site.id.asc()).all()
    return _tpl(
        request,
        "admin.html",
        {
            "user": admin,
            "users": users,
            "sites": sites,
            "error": error,
            "ok": ok,
            "wg_ready": bool(get_settings().wg_server_public_key and get_settings().wg_server_endpoint),
        },
        status_code=400 if error else 200,
    )
