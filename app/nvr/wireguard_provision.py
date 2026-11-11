"""Allocate site tunnel/VPN blocks and render WireGuard peer configs."""

from __future__ import annotations

import base64
import ipaddress
import logging
import shutil
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.ipmap import lan_to_vpn
from nvr.models import Camera, Site
from nvr.security import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


def generate_wg_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64) WireGuard Curve25519 keys."""
    private = X25519PrivateKey.generate()
    priv_b64 = base64.b64encode(private.private_bytes_raw()).decode("ascii")
    pub_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    return priv_b64, pub_b64


def allocate_tunnel_ip(db: Session) -> str:
    settings = get_settings()
    net = ipaddress.ip_network(settings.wg_tunnel_network, strict=False)
    used = {s.tunnel_ip for s in db.query(Site).all() if s.tunnel_ip}
    # .1 is reserved for the VPS server interface.
    for host in net.hosts():
        ip = str(host)
        if ip.endswith(".1"):
            continue
        if ip not in used:
            return ip
    raise ValueError("No free tunnel IPs in wg_tunnel_network")


def allocate_vpn_cidr(db: Session) -> str:
    settings = get_settings()
    base = ipaddress.ip_network(settings.wg_vpn_base, strict=False)
    used = {s.vpn_cidr for s in db.query(Site).all() if s.vpn_cidr}
    # Allocate sequential /24s inside the base (e.g. 10.20.0.0/16 → 10.20.1.0/24 …).
    if base.prefixlen > 24:
        raise ValueError("wg_vpn_base must be at least a /24 container (prefer /16)")
    third_octet_start = 1
    max_n = 2 ** (24 - base.prefixlen) if base.prefixlen < 24 else 1
    # Walk /24 subnets of base, skip 10.20.0.0/24 if base is /16.
    for subnet in base.subnets(new_prefix=24):
        cidr = str(subnet)
        # Prefer starting at *.1.0/24 for readability when base is x.y.0.0/16
        if subnet.network_address.packed[2] < third_octet_start and max_n > 1:
            continue
        if cidr not in used:
            return cidr
    raise ValueError("No free vpn_cidr blocks in wg_vpn_base")


def provision_site_keys(db: Session, site: Site, *, rotate: bool = False) -> Site:
    if site.wg_public_key and site.wg_private_key_enc and not rotate:
        return site
    priv, pub = generate_wg_keypair()
    site.wg_private_key_enc = encrypt_secret(priv)
    site.wg_public_key = pub
    db.commit()
    db.refresh(site)
    write_server_peer_snippet(site)
    apply_peer_hook(site)
    return site


def apply_camera_host_mapping(db: Session, site: Site) -> int:
    """Set camera.host from host_lan via site NETMAP; returns updated count."""
    updated = 0
    cams = db.query(Camera).filter(Camera.site_id == site.id).all()
    for cam in cams:
        lan = (cam.host_lan or cam.host or "").strip()
        if not lan:
            continue
        try:
            mapped = lan_to_vpn(lan, site.lan_cidr, site.vpn_cidr)
        except ValueError:
            continue
        cam.host_lan = lan
        if cam.host != mapped:
            cam.host = mapped
            updated += 1
    db.commit()
    return updated


def client_conf_text(site: Site) -> str:
    settings = get_settings()
    if not site.wg_private_key_enc or not site.wg_public_key:
        raise ValueError("Site has no WireGuard keys; provision first")
    if not settings.wg_server_public_key or not settings.wg_server_endpoint:
        raise ValueError("Configure WG_SERVER_PUBLIC_KEY and WG_SERVER_ENDPOINT")
    private = decrypt_secret(site.wg_private_key_enc)
    iface = site.lan_iface or "eth0"
    lan = site.lan_cidr
    vpn = site.vpn_cidr
    return f"""# ICSEE NVR site: {site.name} (id={site.id})
# Install: sudo cp this file to /etc/wireguard/wg0.conf && sudo systemctl enable --now wg-quick@wg0
[Interface]
Address = {site.tunnel_ip}/32
PrivateKey = {private}
# Map VPN block {vpn} → LAN {lan} (1:1) and NAT replies toward the LAN iface
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A PREROUTING -i wg0 -d {vpn} -j NETMAP --to {lan}; iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o {iface} -j MASQUERADE
PostDown = iptables -t nat -D PREROUTING -i wg0 -d {vpn} -j NETMAP --to {lan}; iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o {iface} -j MASQUERADE

[Peer]
PublicKey = {settings.wg_server_public_key}
Endpoint = {settings.wg_server_endpoint}
AllowedIPs = {settings.wg_server_tunnel_ip}/32
PersistentKeepalive = 25
"""


def server_peer_snippet(site: Site) -> str:
    if not site.wg_public_key:
        raise ValueError("Site has no public key")
    return f"""# peer site id={site.id} {site.name}
[Peer]
PublicKey = {site.wg_public_key}
AllowedIPs = {site.tunnel_ip}/32, {site.vpn_cidr}
PersistentKeepalive = 25
"""


def write_server_peer_snippet(site: Site) -> Path | None:
    settings = get_settings()
    peers_dir = Path(settings.wg_peers_dir)
    try:
        peers_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create wg peers dir %s: %s", peers_dir, exc)
        return None
    path = peers_dir / f"site-{site.id}.conf"
    path.write_text(server_peer_snippet(site), encoding="utf-8")
    return path


def apply_peer_hook(site: Site) -> bool:
    """Run optional host command to sync the peer into the live wg0 interface."""
    settings = get_settings()
    cmd = (settings.wg_apply_cmd or "").strip()
    if not cmd:
        return False
    snippet = write_server_peer_snippet(site)
    try:
        subprocess.run(
            [cmd, str(site.id), site.tunnel_ip, site.vpn_cidr, site.wg_public_key or ""],
            check=True,
            timeout=30,
            capture_output=True,
            text=True,
        )
        logger.info("WG apply cmd ok for site %s (snippet=%s)", site.id, snippet)
        return True
    except Exception as exc:
        logger.warning("WG apply cmd failed for site %s: %s", site.id, exc)
        return False


def which_wg() -> str | None:
    return shutil.which("wg")
