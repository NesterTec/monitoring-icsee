from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from nvr.camera_monitor import first_auth_match
from nvr.ipmap import vpn_to_lan
from nvr.models import Camera, Site
from nvr.notifications import clear_camera_lost
from nvr.security import decrypt_secret


def reattach_from_scanned_hosts(
    db: Session,
    hosts: list[str],
    port: int = 554,
    *,
    site_ids: list[int] | None = None,
    auth_first_fn=None,
    now: datetime | None = None,
) -> dict:
    """
    For cameras whose current host was NOT found in this scan, try each camera's
    saved credentials against unregistered (free) hosts. First auth match updates
    that camera's host in place.
    """
    now = now or datetime.now()
    auth_first = auth_first_fn or first_auth_match
    q = db.query(Camera).order_by(Camera.id.asc())
    if site_ids is not None:
        q = q.filter(Camera.site_id.in_(site_ids))
    cameras = q.all()
    scanned = {h.strip() for h in hosts if h and h.strip()}
    registered = {c.host.strip() for c in cameras if c.host and c.host.strip()}
    free = [h for h in hosts if h.strip() and h.strip() not in registered]
    sites = {
        s.id: s
        for s in db.query(Site).filter(Site.id.in_({c.site_id for c in cameras if c.site_id})).all()
    }

    updated: list[dict] = []
    skipped: list[dict] = []
    unmatched: list[dict] = []

    for cam in cameras:
        current = (cam.host or "").strip()
        if current in scanned:
            skipped.append(
                {
                    "camera_id": cam.id,
                    "name": cam.name,
                    "host": current,
                    "reason": "still_visible",
                }
            )
            continue
        if not free:
            unmatched.append(
                {
                    "camera_id": cam.id,
                    "name": cam.name,
                    "reason": "no_free_hosts",
                }
            )
            continue

        password = decrypt_secret(cam.password_enc)
        match = auth_first(list(free), cam.username, password, port)
        if not match:
            unmatched.append(
                {
                    "camera_id": cam.id,
                    "name": cam.name,
                    "reason": "no_auth_match",
                }
            )
            continue

        old_host = cam.host
        cam.host = match
        site = sites.get(cam.site_id) if cam.site_id else None
        if site:
            try:
                cam.host_lan = vpn_to_lan(match, site.lan_cidr, site.vpn_cidr)
            except ValueError:
                cam.host_lan = match
        else:
            cam.host_lan = match
        cam.fail_count = 0
        cam.reachability_status = "ok"
        cam.last_seen_at = now
        free = [h for h in free if h != match]
        db.commit()
        clear_camera_lost(db, cam.id)
        updated.append(
            {
                "camera_id": cam.id,
                "name": cam.name,
                "old_host": old_host,
                "host": match,
                "host_lan": cam.host_lan,
            }
        )

    return {
        "updated": updated,
        "unmatched": unmatched,
        "skipped": skipped,
        "free_remaining": free,
    }
