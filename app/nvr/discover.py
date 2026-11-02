from __future__ import annotations

import ipaddress
import logging
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from nvr.rtsp import build_rtsp_url

logger = logging.getLogger(__name__)


def guess_default_cidr() -> str:
    """Best-effort LAN CIDR from the host that owns the default route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
    except OSError:
        local_ip = "192.168.1.1"

    # Prefer /24 for home LANs; keep host's network if already private.
    try:
        ip = ipaddress.ip_address(local_ip)
        if ip.is_private:
            network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            return str(network)
    except ValueError:
        pass
    return "192.168.1.0/24"


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_rtsp_hosts(
    cidr: str,
    port: int = 554,
    timeout: float = 0.35,
    max_workers: int = 64,
) -> list[str]:
    network = ipaddress.ip_network(cidr, strict=False)
    if network.num_addresses > 1024:
        raise ValueError("CIDR grande demais (máx. 1024 hosts)")
    hosts = [str(ip) for ip in network.hosts()]
    found: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_tcp_open, host, port, timeout): host for host in hosts}
        for fut in as_completed(futures):
            host = futures[fut]
            try:
                if fut.result():
                    found.append(host)
            except Exception:
                logger.debug("scan failed host=%s", host, exc_info=True)
    return sorted(found, key=lambda h: ipaddress.ip_address(h))


def probe_rtsp_auth(
    host: str,
    username: str,
    password: str,
    port: int = 554,
    channel: int = 1,
    stream: int = 1,
    timeout: float = 6.0,
) -> tuple[bool, str]:
    """Validate device credentials with ffprobe (handles Digest)."""
    url = build_rtsp_url(host, port, username, password, channel, stream)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(int(timeout * 1_000_000)),
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode == 0 and ("video" in out.lower() or out):
        return True, "RTSP autenticado"
    detail = out.splitlines()[-1] if out else f"ffprobe exit {result.returncode}"
    return False, detail[:240]


def auth_probe_hosts(
    hosts: list[str],
    username: str,
    password: str,
    port: int = 554,
    max_workers: int = 8,
) -> list[dict]:
    results: list[dict] = []

    def one(host: str) -> dict:
        ok, detail = probe_rtsp_auth(host, username, password, port=port)
        return {"host": host, "ok": ok, "detail": detail}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one, host) for host in hosts]
        for fut in as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda r: (not r["ok"], ipaddress.ip_address(r["host"])))
    return results


def known_camera_hosts(hosts_from_db: list[str]) -> set[str]:
    return {h.strip() for h in hosts_from_db if h and h.strip()}
