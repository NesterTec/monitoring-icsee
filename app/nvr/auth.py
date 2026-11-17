from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.db import get_db
from nvr.oidc import require_user_redirect
from nvr.models import ROLE_SUPERADMIN, ROLE_USER, Camera, Site, User
from nvr.security import hash_password, verify_password
from nvr.wireguard_provision import allocate_tunnel_ip, allocate_vpn_cidr


def is_superadmin(user: User) -> bool:
    return (user.role or "") == ROLE_SUPERADMIN


def seed_admin(db: Session) -> None:
    settings = get_settings()
    user = db.query(User).filter(User.email == settings.admin_email).one_or_none()
    if user is None:
        user = User(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            role=ROLE_SUPERADMIN,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if user.role != ROLE_SUPERADMIN:
            user.role = ROLE_SUPERADMIN
            db.commit()
    ensure_default_site(db, user)


def ensure_default_site(db: Session, owner: User) -> Site:
    """Create default site for superadmin and attach orphan cameras (migration)."""
    site = (
        db.query(Site)
        .filter(Site.owner_user_id == owner.id)
        .order_by(Site.id.asc())
        .first()
    )
    if site is None:
        # Infer LAN from first camera host if possible.
        lan_cidr = "192.168.1.0/24"
        first = db.query(Camera).order_by(Camera.id.asc()).first()
        if first and first.host and first.host.count(".") == 3:
            parts = first.host.strip().split(".")
            if parts[0].isdigit():
                lan_cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        site = Site(
            name="Casa Nestertec",
            owner_user_id=owner.id,
            lan_cidr=lan_cidr,
            vpn_cidr=allocate_vpn_cidr(db),
            tunnel_ip=allocate_tunnel_ip(db),
            lan_iface="eth0",
            enabled=True,
        )
        db.add(site)
        db.commit()
        db.refresh(site)

    orphans = db.query(Camera).filter(Camera.site_id.is_(None)).all()
    for cam in orphans:
        cam.site_id = site.id
        if not cam.host_lan:
            cam.host_lan = cam.host
    if orphans:
        db.commit()
    return site


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        # SSO_REQUIRED: navegação HTML segue para o OIDC; demais chamadas mantêm 401.
        redirect = require_user_redirect(request)
        if redirect is not None:
            raise redirect
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_superadmin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_user(request, db)
    if not is_superadmin(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


def user_sites(db: Session, user: User) -> list[Site]:
    if is_superadmin(user):
        return db.query(Site).order_by(Site.id.asc()).all()
    return (
        db.query(Site)
        .filter(Site.owner_user_id == user.id, Site.enabled.is_(True))
        .order_by(Site.id.asc())
        .all()
    )


def user_site_ids(db: Session, user: User) -> list[int]:
    return [s.id for s in user_sites(db, user)]


def camera_for_user(db: Session, user: User, camera_id: int) -> Camera | None:
    cam = db.get(Camera, camera_id)
    if cam is None:
        return None
    if is_superadmin(user):
        return cam
    if cam.site_id is None or cam.site_id not in user_site_ids(db, user):
        return None
    return cam


def primary_site_for_user(db: Session, user: User) -> Site | None:
    sites = user_sites(db, user)
    if not sites:
        return None
    if is_superadmin(user):
        owned = [s for s in sites if s.owner_user_id == user.id]
        return owned[0] if owned else sites[0]
    return sites[0]


def create_user_with_site(
    db: Session,
    *,
    email: str,
    password: str,
    site_name: str,
    lan_cidr: str,
    lan_iface: str = "eth0",
) -> tuple[User, Site]:
    if db.query(User).filter(User.email == email).one_or_none():
        raise ValueError("E-mail já cadastrado")
    user = User(email=email.strip(), password_hash=hash_password(password), role=ROLE_USER)
    db.add(user)
    db.commit()
    db.refresh(user)
    site = Site(
        name=site_name.strip() or f"Site {user.email}",
        owner_user_id=user.id,
        lan_cidr=lan_cidr.strip() or "192.168.1.0/24",
        vpn_cidr=allocate_vpn_cidr(db),
        tunnel_ip=allocate_tunnel_ip(db),
        lan_iface=lan_iface.strip() or "eth0",
        enabled=True,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return user, site
