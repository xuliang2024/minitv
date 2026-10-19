"""HTTP contract for coin unlocks: key required, 402 shape, replay, guards."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts
from app.modules.wallet.models import CoinLedgerEntry, Entitlement


@pytest.fixture()
def client(session):
    return TestClient(app)


def _auth(client, monkeypatch, email: str = "unlock@example.com") -> dict[str, str]:
    monkeypatch.setattr(accounts, "generate_code", lambda: "123456")
    monkeypatch.setattr(accounts, "send_verification_code", lambda **_: "test-message")
    sent = client.post("/v1/auth/email/code", json={"email": email, "purpose": "login"})
    assert sent.status_code == 200, sent.text
    response = client.post(
        "/v1/auth/email/authenticate", json={"email": email, "code": "123456"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_unlock_requires_idempotency_key(make, client, monkeypatch):
    headers = _auth(client, monkeypatch)
    drama = make.drama(status="published", price=50)
    episode = make.episode(drama, number=5, status="published")

    response = client.post("/v1/unlock", json={"episode_id": episode.id}, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Idempotency-Key required"


def test_unlock_replay_charges_once(make, session, client, monkeypatch):
    headers = _auth(client, monkeypatch)
    drama = make.drama(status="published", price=50)
    episode = make.episode(drama, number=5, status="published")
    payload = {"episode_id": episode.id}
    keyed = {**headers, "Idempotency-Key": "http-replay"}

    first = client.post("/v1/unlock", json=payload, headers=keyed)
    second = client.post("/v1/unlock", json=payload, headers=keyed)

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "unlocked"
    assert first.json()["balance_after"] == 30  # 80 register reward - 50
    assert first.json()["replayed"] is False

    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert second.json()["balance_after"] == 30

    assert session.query(CoinLedgerEntry).filter(CoinLedgerEntry.biz_type == "unlock").count() == 1
    assert session.query(Entitlement).count() == 1


def test_unlock_insufficient_returns_need_and_have(make, client, monkeypatch):
    headers = _auth(client, monkeypatch)
    drama = make.drama(status="published", price=100)
    episode = make.episode(drama, number=5, status="published")

    response = client.post(
        "/v1/unlock",
        json={"episode_id": episode.id},
        headers={**headers, "Idempotency-Key": "http-broke"},
    )

    assert response.status_code == 402
    assert response.json()["detail"] == {"need": 100, "have": 80}


def test_unlock_zero_price_is_rejected_not_a_500(make, client, monkeypatch):
    headers = _auth(client, monkeypatch)
    drama = make.drama(status="published", price=0)
    episode = make.episode(drama, number=5, status="published")

    response = client.post(
        "/v1/unlock",
        json={"episode_id": episode.id},
        headers={**headers, "Idempotency-Key": "http-free"},
    )

    assert response.status_code == 409


def test_unlock_unpublished_episode_is_404(make, client, monkeypatch):
    headers = _auth(client, monkeypatch)
    drama = make.drama(status="published", price=50)
    episode = make.episode(drama, number=5, status="in_review")

    response = client.post(
        "/v1/unlock",
        json={"episode_id": episode.id},
        headers={**headers, "Idempotency-Key": "http-hidden"},
    )

    assert response.status_code == 404
