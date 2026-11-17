"""OIDC relying party (Authorization Code + PKCE) against NesterAdmin."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.models import User

SESSION_FLOW = "oidc_flow"
_CACHE_TTL = 3600
_cache: dict[str, tuple[float, Any]] = {}


class OidcError(Exception):
    """User-safe error message."""


def oidc_enabled() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id and s.oidc_client_secret and s.oidc_redirect_uri)


def sso_required() -> bool:
    return get_settings().sso_required and oidc_enabled()


def _issuer() -> str:
    return get_settings().oidc_issuer.rstrip("/")


def _cached(key: str, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    _cache.clear()


def discovery() -> dict[str, Any]:
    if not oidc_enabled():
        raise OidcError("SSO não configurado.")

    def load():
        try:
            r = httpx.get(f"{_issuer()}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            doc = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError("Não foi possível obter a configuração do SSO.") from exc
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if k not in doc:
                raise OidcError("Configuração do SSO incompleta.")
        return doc

    return _cached("discovery", load)


def _jwks(uri: str) -> jwt.PyJWKSet:
    def load():
        try:
            r = httpx.get(uri, timeout=10)
            r.raise_for_status()
            return jwt.PyJWKSet.from_dict(r.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            raise OidcError("Não foi possível obter as chaves do SSO.") from exc

    return _cached("jwks", load)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_authorization_url(request: Request) -> str:
    doc = discovery()
    s = get_settings()
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session[SESSION_FLOW] = {"state": state, "nonce": nonce, "verifier": verifier}
    query = urlencode(
        {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": s.oidc_redirect_uri,
            "scope": s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{doc['authorization_endpoint']}?{query}"


def _validate_id_token(token: str, doc: dict[str, Any], nonce: str) -> dict[str, Any]:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        keyset = _jwks(doc["jwks_uri"])
        key = next((k for k in keyset.keys if k.key_id == header.get("kid")), None)
        if key is None:
            clear_cache()  # chave pode ter rotacionado
            raise OidcError("ID token SSO inválido (kid).")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=s.oidc_client_id,
            leeway=60,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except OidcError:
        raise
    except jwt.PyJWTError as exc:
        raise OidcError("ID token SSO inválido.") from exc
    if str(claims.get("iss", "")).rstrip("/") != _issuer():
        raise OidcError("ID token SSO inválido (issuer).")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("ID token SSO inválido (nonce).")
    return claims


def complete_login(request: Request, db: Session) -> User:
    """Handle the callback: exchange code, validate ID token, set session user."""
    flow = request.session.pop(SESSION_FLOW, None)
    if not isinstance(flow, dict):
        raise OidcError("Sessão SSO expirada. Tente novamente.")
    if request.query_params.get("error"):
        raise OidcError("SSO recusado pelo provedor de identidade.")
    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    if not code or not secrets.compare_digest(flow["state"], state):
        raise OidcError("Resposta SSO inválida (state).")

    s = get_settings()
    doc = discovery()
    try:
        resp = httpx.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.oidc_redirect_uri,
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret,
                "code_verifier": flow["verifier"],
            },
            timeout=15,
        )
        id_token = resp.json().get("id_token") if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("Falha ao trocar o código SSO por token.") from exc
    if not id_token:
        raise OidcError("Falha ao trocar o código SSO por token.")

    claims = _validate_id_token(id_token, doc, flow["nonce"])
    return _login_from_claims(request, db, claims)


def _login_from_claims(request: Request, db: Session, claims: dict[str, Any]) -> User:
    sub = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise OidcError("O SSO não retornou e-mail.")
    if claims.get("email_verified") is False:
        raise OidcError("E-mail do SSO não verificado.")

    user = db.query(User).filter(User.oidc_sub == sub).one_or_none()
    if user is None:
        user = db.query(User).filter(User.email.ilike(email)).first()
    if user is None:
        raise OidcError("Seu e-mail não tem acesso ao ICSee.")
    if user.oidc_sub and user.oidc_sub != sub:
        raise OidcError("Conta já vinculada a outra identidade SSO.")
    if user.oidc_sub != sub:
        user.oidc_sub = sub
        db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["oidc_sub"] = sub
    return user


def require_user_redirect(request: Request) -> HTTPException | None:
    """When SSO_REQUIRED, browser GETs get a redirect to the OIDC login instead of 401."""
    if not sso_required() or request.method != "GET":
        return None
    if "text/html" not in request.headers.get("accept", "") and request.headers.get("accept"):
        return None
    from nvr.urls import abs_path

    return HTTPException(status_code=303, headers={"Location": abs_path("/auth/oidc/login")})
