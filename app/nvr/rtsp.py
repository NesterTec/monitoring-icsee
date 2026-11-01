from urllib.parse import quote


def build_rtsp_url(
    host: str,
    port: int,
    username: str,
    password: str,
    channel: int = 1,
    stream: int = 1,
) -> str:
    user_q = quote(username, safe="")
    pass_q = quote(password, safe="")
    path = f"user={user_q}&password={pass_q}&channel={channel}&stream={stream}.sdp?"
    return f"rtsp://{user_q}:{pass_q}@{host}:{port}/{path}"


def probe_rtsp(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Best-effort TCP + RTSP OPTIONS. Returns (ok, detail)."""
    import re
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 554
    if not host:
        return False, "invalid RTSP URL"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            req = (
                f"OPTIONS {url} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"User-Agent: icsee-nvr\r\n\r\n"
            )
            sock.sendall(req.encode())
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            text = data.decode(errors="replace")
            status = text.split("\n", 1)[0].strip()
            if re.search(r"RTSP/1\.0 200", status) or "Public:" in text or "401" in status:
                # 401 means server reachable and speaking RTSP (auth required)
                if "401" in status:
                    return True, "RTSP reachable (auth required / ok)"
                return True, status or "RTSP OK"
            return False, status or "unexpected RTSP response"
    except OSError as exc:
        return False, str(exc)
