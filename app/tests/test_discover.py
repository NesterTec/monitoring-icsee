import ipaddress

from nvr.discover import guess_default_cidr, scan_rtsp_hosts


def test_guess_default_cidr_is_valid_network():
    cidr = guess_default_cidr()
    net = ipaddress.ip_network(cidr, strict=False)
    assert net.version == 4
    assert net.prefixlen >= 24


def test_scan_rejects_huge_cidr():
    try:
        scan_rtsp_hosts("10.0.0.0/8")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_discover_scan_endpoint(auth_client, monkeypatch):
    monkeypatch.setattr("nvr.routers.web.scan_rtsp_hosts", lambda cidr, port=554: ["192.168.1.15", "192.168.1.10"])
    r = auth_client.post("/cameras/discover/scan", json={"cidr": "192.168.1.0/24", "port": 554})
    assert r.status_code == 200
    body = r.json()
    assert body["hosts"][0]["host"] == "192.168.1.10" or body["hosts"][0]["host"] == "192.168.1.15"
    assert len(body["hosts"]) == 2


def test_discover_auth_endpoint(auth_client, monkeypatch):
    monkeypatch.setattr(
        "nvr.routers.web.auth_probe_hosts",
        lambda hosts, username, password, port=554: [
            {"host": "192.168.1.15", "ok": True, "detail": "RTSP autenticado"},
            {"host": "192.168.1.10", "ok": False, "detail": "auth failed"},
        ],
    )
    r = auth_client.post(
        "/cameras/discover/auth",
        json={
            "hosts": ["192.168.1.10", "192.168.1.15"],
            "username": "device_user",
            "password": "device_pass",
            "port": 554,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["matches"]) == 1
    assert body["matches"][0]["host"] == "192.168.1.15"
