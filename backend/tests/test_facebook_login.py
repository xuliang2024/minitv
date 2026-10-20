"""Facebook sign-in: token shapes, account linking, and what the client sees.

Two token shapes reach `/v1/auth/facebook` — a Limited Login id_token (JWT) and
an opaque SDK access token. Both end up as the same session token email
sign-in returns. The verifier itself is exercised here for the checks that
matter: the audience/app check, and the profile mapping.
"""

from __future__ import annotations

import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import facebook
from app.modules.account import service as accounts
from app.modules.account.facebook import FacebookAuthError, FacebookIdentity
from app.modules.account.models import ThirdPartyIdentity, User


@pytest.fixture()
def client(session):
    return TestClient(app)


def _identity(**overrides) -> FacebookIdentity:
    base = dict(subject="fb-100", email="fb.user@example.com", name="Fb User", picture="https://cdn/fb.jpg")
    base.update(overrides)
    return FacebookIdentity(**base)


def test_providers_hides_facebook_until_an_app_id_exists(client, monkeypatch):
    monkeypatch.delenv("FACEBOOK_APP_ID", raising=False)
    data = client.get("/v1/auth/providers").json()
    assert data["facebook"] == {"enabled": False, "app_id": None}


def test_providers_serves_the_public_app_id(client, monkeypatch):
    monkeypatch.setenv("FACEBOOK_APP_ID", "1234567890")
    data = client.get("/v1/auth/providers").json()
    assert data["facebook"] == {"enabled": True, "app_id": "1234567890"}


def test_access_token_signs_up_then_signs_in(client, session, monkeypatch):
    monkeypatch.setattr(accounts, "verify_facebook", lambda **_: _identity())

    first = client.post("/v1/auth/facebook", json={"access_token": "opaque-token"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert body["token"]

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "fb.user@example.com"
    assert me.json()["display_name"] == "Fb User"

    again = client.post("/v1/auth/facebook", json={"access_token": "opaque-token-2"})
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["user_id"] == body["user_id"]
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(func.count()).select_from(ThirdPartyIdentity)) == 1


def test_limited_login_id_token_is_accepted(client, monkeypatch):
    seen: dict[str, str | None] = {}

    def verify(*, access_token=None, id_token=None):
        seen["id_token"] = id_token
        seen["access_token"] = access_token
        return _identity(subject="fb-limited")

    monkeypatch.setattr(accounts, "verify_facebook", verify)
    response = client.post("/v1/auth/facebook", json={"id_token": "signed.jwt.value"})
    assert response.status_code == 200, response.text
    assert seen == {"id_token": "signed.jwt.value", "access_token": None}


def test_a_missing_token_is_rejected_before_any_work(client):
    response = client.post("/v1/auth/facebook", json={})
    assert response.status_code == 422
    assert response.json()["detail"] == "access_token or id_token required"


def test_a_refused_token_is_a_401(client, monkeypatch):
    def refuse(**_):
        raise FacebookAuthError("facebook access token is invalid")

    monkeypatch.setattr(accounts, "verify_facebook", refuse)
    response = client.post("/v1/auth/facebook", json={"access_token": "expired"})
    assert response.status_code == 401
    assert response.json()["detail"] == "facebook access token is invalid"


def test_confirmed_email_lands_on_the_existing_account(client, session, make, monkeypatch):
    existing = make.user(email="fb.user@example.com", coins=20)
    monkeypatch.setattr(accounts, "verify_facebook", lambda **_: _identity())

    response = client.post("/v1/auth/facebook", json={"access_token": "opaque"})
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == existing.id
    assert response.json()["created"] is False
    link = session.scalar(select(ThirdPartyIdentity).where(ThirdPartyIdentity.provider == "facebook"))
    assert link is not None and link.user_id == existing.id


def test_merging_can_be_switched_off(client, make, monkeypatch):
    existing = make.user(email="fb.user@example.com")
    monkeypatch.setenv("FACEBOOK_LINK_BY_EMAIL", "0")
    monkeypatch.setattr(accounts, "verify_facebook", lambda **_: _identity())

    response = client.post("/v1/auth/facebook", json={"access_token": "opaque"})
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["user_id"] != existing.id


def test_verify_access_token_refuses_another_app(monkeypatch):
    monkeypatch.setenv("FACEBOOK_APP_ID", "123")
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "secret")

    def fake_request(path, params):
        if path.endswith("/debug_token"):
            return {"data": {"is_valid": True, "app_id": "999", "user_id": "7"}}
        raise AssertionError("must not fetch the profile of a foreign token")

    monkeypatch.setattr(facebook, "_request_json", fake_request)
    with pytest.raises(FacebookAuthError, match="another app"):
        facebook.verify_access_token("opaque")


def test_verify_access_token_maps_the_profile(monkeypatch):
    monkeypatch.setenv("FACEBOOK_APP_ID", "123")
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "secret")

    def fake_request(path, params):
        if path.endswith("/debug_token"):
            assert params["access_token"] == "123|secret"
            return {"data": {"is_valid": True, "app_id": "123", "user_id": "42"}}
        return {
            "id": "42",
            "name": "  Ada  ",
            "email": "Ada@Example.COM",
            "picture": {"data": {"url": "https://cdn/ad a.jpg"}},
        }

    monkeypatch.setattr(facebook, "_request_json", fake_request)
    identity = facebook.verify_access_token("opaque")
    assert identity.subject == "42"
    assert identity.email == "ada@example.com"
    assert identity.name == "Ada"
    assert identity.picture == "https://cdn/ad a.jpg"
    assert identity.via == "graph"


def test_verify_id_token_checks_the_audience(monkeypatch):
    monkeypatch.setenv("FACEBOOK_APP_ID", "123")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-kid"
    monkeypatch.setattr(facebook, "_certs", lambda: [jwk])

    claims = {
        "iss": facebook.FACEBOOK_ISSUER,
        "aud": "123",
        "sub": "fb-limited-1",
        "email": "Limited@Example.com",
        "name": "Limited User",
        "picture": "https://cdn/limited.jpg",
    }
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})
    identity = facebook.verify_id_token(token)
    assert identity.subject == "fb-limited-1"
    assert identity.email == "limited@example.com"
    assert identity.via == "limited_login"

    foreign = jwt.encode({**claims, "aud": "999"}, key, algorithm="RS256", headers={"kid": "test-kid"})
    with pytest.raises(FacebookAuthError):
        facebook.verify_id_token(foreign)
