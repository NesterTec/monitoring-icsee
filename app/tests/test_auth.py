def test_login_ok(client):
    r = client.post(
        "/login",
        data={"email": "ivanrank@yahoo.com.br", "password": "secret"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)


def test_dashboard_requires_auth(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
