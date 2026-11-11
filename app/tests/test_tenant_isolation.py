import nvr.db as db_module
from nvr.auth import create_user_with_site, seed_admin
from nvr.models import Camera, User
from nvr.security import encrypt_secret
from nvr.wireguard_provision import client_conf_text, provision_site_keys


def _login(client, email: str, password: str):
    r = client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_seed_superadmin_and_default_site(client):
    db = db_module.SessionLocal()
    try:
        seed_admin(db)
        admin = db.query(User).filter(User.email == "ivanrank@yahoo.com.br").one()
        assert admin.role == "superadmin"
        assert len(admin.sites) >= 1
    finally:
        db.close()


def test_user_cannot_see_other_site_camera(client, monkeypatch):
    monkeypatch.setenv("WG_SERVER_PUBLIC_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("WG_SERVER_ENDPOINT", "203.0.113.10:51820")
    from nvr.config import get_settings

    get_settings.cache_clear()

    db = db_module.SessionLocal()
    try:
        seed_admin(db)
        admin = db.query(User).filter(User.email == "ivanrank@yahoo.com.br").one()
        site_a = admin.sites[0]
        cam_a = Camera(
            site_id=site_a.id,
            name="AdminCam",
            host="10.20.1.10",
            host_lan="192.168.1.10",
            username="u",
            password_enc=encrypt_secret("p"),
            sort_order=0,
        )
        db.add(cam_a)
        user_b, site_b = create_user_with_site(
            db,
            email="tenant@example.com",
            password="secret12",
            site_name="Casa B",
            lan_cidr="192.168.1.0/24",
        )
        cam_b = Camera(
            site_id=site_b.id,
            name="TenantCam",
            host="10.20.2.20",
            host_lan="192.168.1.20",
            username="u",
            password_enc=encrypt_secret("p"),
            sort_order=0,
            enabled=True,
            dashboard_visible=True,
        )
        db.add(cam_b)
        db.commit()
        cam_a_id, cam_b_id = cam_a.id, cam_b.id
    finally:
        db.close()

    _login(client, "tenant@example.com", "secret12")
    dash = client.get("/")
    assert dash.status_code == 200
    assert "TenantCam" in dash.text
    assert "AdminCam" not in dash.text

    denied = client.get(f"/cameras/{cam_a_id}/edit", follow_redirects=False)
    assert denied.status_code == 404

    ok = client.get(f"/cameras/{cam_b_id}/edit", follow_redirects=False)
    assert ok.status_code == 200


def test_client_conf_contains_netmap(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WG_SERVER_PUBLIC_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("WG_SERVER_ENDPOINT", "203.0.113.10:51820")
    monkeypatch.setenv("WG_PEERS_DIR", str(tmp_path / "peers"))
    from nvr.config import get_settings

    get_settings.cache_clear()

    db = db_module.SessionLocal()
    try:
        seed_admin(db)
        user, site = create_user_with_site(
            db,
            email="wguser@example.com",
            password="secret12",
            site_name="WG Home",
            lan_cidr="192.168.1.0/24",
            lan_iface="eth0",
        )
        provision_site_keys(db, site)
        db.refresh(site)
        conf = client_conf_text(site)
        assert site.tunnel_ip in conf
        assert site.vpn_cidr in conf
        assert "NETMAP" in conf
        assert site.lan_cidr in conf
        assert "203.0.113.10:51820" in conf
    finally:
        db.close()

    _login(client, "wguser@example.com", "secret12")
    r = client.get("/site/wireguard.conf")
    assert r.status_code == 200
    assert "NETMAP" in r.text
    assert "attachment" in r.headers.get("content-disposition", "")
