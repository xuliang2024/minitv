"""Account sign-in: one email-code door, plus what the client may offer.

The H5 has no login screen of its own knowledge: it asks ``/v1/auth/providers``
what this deployment supports, and it never has to guess whether an email is
already registered. These tests pin both behaviours down.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts


@pytest.fixture()
def client(session):
    return TestClient(app)


@pytest.fixture()
def outbox(monkeypatch):
    """Capture the plaintext code instead of calling Resend."""
    sent: list[dict] = []

    def fake_send(*, to: str, code: str, purpose: str) -> str:
        sent.append({"to": to, "code": code, "purpose": purpose})
        return "test-message-id"

    monkeypatch.setattr(accounts, "send_verification_code", fake_send)
    return sent


def _send_code(client, email: str, purpose: str | None = None) -> dict:
    body = {"email": email}
    if purpose is not None:
        body["purpose"] = purpose
    response = client.post("/v1/auth/email/code", json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"sent": True}
    return body


def _authenticate(client, email: str, code: str):
    return client.post("/v1/auth/email/authenticate", json={"email": email, "code": code})


def test_providers_reports_only_usable_methods(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    data = client.get("/v1/auth/providers").json()
    assert data["email"]["enabled"] is True
    assert data["google"] == {"enabled": False, "client_id": None}


def test_providers_serves_google_client_id(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc.apps.googleusercontent.com")
    data = client.get("/v1/auth/providers").json()
    assert data["google"] == {
        "enabled": True,
        "client_id": "abc.apps.googleusercontent.com",
    }


def test_code_defaults_to_login_purpose(client, outbox, session):
    _send_code(client, "New@Example.com")
    assert len(outbox) == 1
    assert outbox[0]["to"] == "new@example.com"
    assert outbox[0]["purpose"] == "login"

    from app.modules.account.models import VerificationCode
    from sqlalchemy import select

    rows = session.scalars(select(VerificationCode)).all()
    assert [row.purpose for row in rows] == ["login"]


def test_invalid_purpose_is_rejected(client, outbox):
    response = client.post("/v1/auth/email/code", json={"email": "a@b.com", "purpose": "reset"})
    assert response.status_code == 422
    assert outbox == []


def test_authenticate_creates_then_reuses_the_same_account(client, outbox, session):
    _send_code(client, "viewer@example.com")
    first = _authenticate(client, "viewer@example.com", outbox[0]["code"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert body["token"]
    assert body["public_id"]

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "viewer@example.com"

    _send_code(client, "viewer@example.com")
    second = _authenticate(client, "viewer@example.com", outbox[1]["code"])
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["user_id"] == body["user_id"]

    from sqlalchemy import func, select
    from app.modules.account.models import User

    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_code_is_single_use(client, outbox):
    _send_code(client, "once@example.com")
    code = outbox[0]["code"]
    assert _authenticate(client, "once@example.com", code).status_code == 200
    assert _authenticate(client, "once@example.com", code).status_code in (400, 404)


def test_wrong_code_does_not_create_an_account(client, outbox, session):
    _send_code(client, "typo@example.com")
    wrong = "000000" if outbox[0]["code"] != "000000" else "111111"
    response = _authenticate(client, "typo@example.com", wrong)
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid code"

    from sqlalchemy import func, select
    from app.modules.account.models import User

    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_legacy_login_endpoint_still_refuses_unknown_email(client, outbox):
    _send_code(client, "ghost@example.com")
    response = client.post(
        "/v1/auth/email/login",
        json={"email": "ghost@example.com", "code": outbox[0]["code"]},
    )
    assert response.status_code == 404


def test_reactions_need_a_session_and_feed_the_play_payload(client, outbox, make, session):
    drama = make.drama(title="Social", status="published")
    make.episode(drama, number=1, status="published")

    assert client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": False},
    ).status_code == 401

    _send_code(client, "fan@example.com")
    token = _authenticate(client, "fan@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    liked = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": False},
        headers=headers,
    )
    assert liked.status_code == 200
    assert liked.json() == {"active": True}

    favorited = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "favorite", "remove": False},
        headers=headers,
    )
    assert favorited.json() == {"active": True}

    play = client.get(f"/v1/dramas/{drama.id}/play", headers=headers)
    assert play.status_code == 200
    drama_body = play.json()["drama"]
    assert drama_body["like_count"] == 1
    assert drama_body["liked"] is True
    assert drama_body["favorited"] is True

    guest = client.get(f"/v1/dramas/{drama.id}/play").json()["drama"]
    assert guest["like_count"] == 1
    assert guest["liked"] is False
    assert guest["favorited"] is False

    removed = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": True},
        headers=headers,
    )
    assert removed.json() == {"active": False}
    assert client.get(f"/v1/dramas/{drama.id}/play", headers=headers).json()["drama"]["like_count"] == 0
