import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test/nesteradmin"


@pytest.fixture()
def oidc_env(app_env, monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", "icsee")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.test/auth/oidc/callback")
    from nvr import oidc
    from nvr.config import get_settings

    get_settings.cache_clear()
    oidc.clear_cache()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    oidc._cache["discovery"] = (
        time.time(),
        {
            "authorization_endpoint": f"{ISSUER}/oauth/authorize",
            "token_endpoint": f"{ISSUER}/oauth/token",
            "jwks_uri": f"{ISSUER}/oauth/jwks",
        },
    )
    oidc._cache["jwks"] = (time.time(), jwt.PyJWKSet.from_dict({"keys": [jwk]}))
    return key


def _token(key, nonce, **over):
    claims = {
        "iss": ISSUER, "aud": "icsee", "sub": "sub-1", "email": "ivanrank@yahoo.com.br",
        "nonce": nonce, "iat": int(time.time()), "exp": int(time.time()) + 300,
    }
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def test_login_redirects_with_pkce(client, oidc_env):
    r = client.get("/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith(f"{ISSUER}/oauth/authorize?")
    assert "code_challenge_method=S256" in loc and "client_id=icsee" in loc


def _start(client):
    from urllib.parse import parse_qs, urlparse

    loc = client.get("/auth/oidc/login", follow_redirects=False).headers["location"]
    q = parse_qs(urlparse(loc).query)
    return q["state"][0], q["nonce"][0]


def test_callback_sets_session(client, oidc_env, monkeypatch):
    from nvr import oidc

    state, nonce = _start(client)

    class R:
        status_code = 200
        def json(self):
            return {"id_token": _token(oidc_env, nonce)}

    monkeypatch.setattr(oidc.httpx, "post", lambda *a, **k: R())
    r = client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 200


def test_callback_rejects_bad_nonce(client, oidc_env, monkeypatch):
    from nvr import oidc

    state, _ = _start(client)

    class R:
        status_code = 200
        def json(self):
            return {"id_token": _token(oidc_env, "wrong")}

    monkeypatch.setattr(oidc.httpx, "post", lambda *a, **k: R())
    r = client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 403


def test_sso_required_redirects(client, oidc_env, monkeypatch):
    monkeypatch.setenv("SSO_REQUIRED", "true")
    from nvr.config import get_settings

    get_settings.cache_clear()
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].endswith("/auth/oidc/login")
    r = client.get("/login", follow_redirects=False)
    assert r.headers["location"].endswith("/auth/oidc/login")
