"""P3: review queue, batch decisions, and the approved → published hand-off."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.content.models import Drama, Episode


@pytest.fixture()
def client(session):
    return TestClient(app)


def _headers(client, username, password) -> dict:
    r = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin(make, client) -> tuple[dict, dict]:
    user = make.admin(role="admin")
    return _headers(client, user.username, "pass-1234"), {"username": user.username}


def _partner(make, client, headers, name="星野传媒", username="sp-a") -> tuple[dict, dict]:
    sup = client.post(
        "/admin/v1/suppliers",
        json={"name": name, "contact_email": "partner@example.com"},
        headers=headers,
    ).json()
    client.post(
        "/admin/v1/staff",
        json={
            "username": username,
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": sup["id"],
        },
        headers=headers,
    )
    return sup, _headers(client, username, "partner-pass-1234")


def _submitted_drama(session, client, partner, title="待审剧", episodes=5, free=5) -> dict:
    drama = client.post(
        "/partner/v1/dramas",
        json={
            "title": title,
            "synopsis": "一段完整的故事简介，" * 5,
            "free_episode_count": free,
        },
        headers=partner,
    ).json()
    row = session.get(Drama, drama["id"])
    row.cover_url = "https://img.example.com/c.jpg"
    session.commit()
    for number in range(1, episodes + 1):
        session.add(
            Episode(
                drama_id=row.id,
                episode_number=number,
                title=f"第 {number} 集",
                status="in_review",
            )
        )
    session.commit()
    submitted = client.post(f"/partner/v1/dramas/{row.id}/submit", headers=partner)
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


# ---- queue ----


def test_queue_lists_only_submitted_supplier_dramas(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    r = client.get("/admin/v1/reviews?state=pending", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert [item["id"] for item in body["items"]] == [drama["id"]]
    assert body["items"][0]["supplier"]["name"] == "星野传媒"
    assert body["counts"]["pending"] == 1

    mine = make.drama(title="自营剧")
    session.commit()
    listed = client.get("/admin/v1/reviews?state=pending", headers=admin).json()
    assert mine.id not in [item["id"] for item in listed["items"]]


def test_finance_cannot_read_or_decide(make, session, client):
    admin, _ = _admin(make, client)
    finance = make.admin(role="finance")
    fin = _headers(client, finance.username, "pass-1234")
    assert client.get("/admin/v1/reviews", headers=fin).status_code == 403
    assert client.post("/admin/v1/reviews/1/decision", json={"decision": "approve"}, headers=fin).status_code == 403


# ---- single decision ----


def test_approve_keeps_drama_offline(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    r = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "approve"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["review_state"] == "approved"

    row = session.get(Drama, drama["id"])
    assert row.status == "draft"  # approved ≠ published
    assert row.reviewed_by is not None
    assert row.review_note is None


def test_reject_requires_note(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    missing = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "reject"},
        headers=admin,
    )
    assert missing.status_code == 422

    ok = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "reject", "note": "封面尺寸不足"},
        headers=admin,
    )
    assert ok.status_code == 200
    assert ok.json()["review_state"] == "rejected"
    assert ok.json()["review_note"] == "封面尺寸不足"


def test_double_review_conflicts(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    first = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "approve"},
        headers=admin,
    )
    assert first.status_code == 200
    second = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "approve"},
        headers=admin,
    )
    assert second.status_code == 409


# ---- batch ----


def test_batch_skips_already_processed(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    a = _submitted_drama(session, client, partner, title="甲剧")
    b = _submitted_drama(session, client, partner, title="乙剧")
    c = _submitted_drama(session, client, partner, title="丙剧")
    client.post(
        f"/admin/v1/reviews/{c['id']}/decision", json={"decision": "approve"}, headers=admin
    )

    r = client.post(
        "/admin/v1/reviews/batch",
        json={"drama_ids": [a["id"], b["id"], c["id"]], "decision": "approve"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["done"] == 2
    assert [item["id"] for item in body["skipped"]] == [c["id"]]
    assert "不是待审核" in body["skipped"][0]["reason"]

    states = {
        row.id: row.review_state
        for row in session.scalars(
            select(Drama).where(Drama.id.in_([a["id"], b["id"], c["id"]]))
        )
    }
    assert states == {a["id"]: "approved", b["id"]: "approved", c["id"]: "approved"}


def test_batch_rejects_without_note_and_over_limit(make, session, client):
    admin, _ = _admin(make, client)
    no_note = client.post(
        "/admin/v1/reviews/batch",
        json={"drama_ids": [1, 2], "decision": "reject"},
        headers=admin,
    )
    assert no_note.status_code == 422

    too_many = client.post(
        "/admin/v1/reviews/batch",
        json={"drama_ids": list(range(1, 52)), "decision": "approve"},
        headers=admin,
    )
    assert too_many.status_code == 422


# ---- publish ----


def test_publish_requires_approval(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    blocked = client.post(f"/admin/v1/dramas/{drama['id']}/status", json={"to_status": "published"}, headers=admin)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "not_approved"


def test_publish_after_approval_publishes_episodes(make, session, client):
    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner, episodes=3, free=3)
    client.post(
        f"/admin/v1/reviews/{drama['id']}/decision", json={"decision": "approve"}, headers=admin
    )

    r = client.post(f"/admin/v1/reviews/{drama['id']}/publish", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    row = session.get(Drama, drama["id"])
    assert row.status == "published" and row.published_at is not None
    ep_states = session.scalars(
        select(Episode.status).where(Episode.drama_id == drama["id"])
    ).all()
    assert set(ep_states) == {"published"}

    again = client.post(f"/admin/v1/reviews/{drama['id']}/publish", headers=admin)
    assert again.status_code == 409


def test_approval_sends_email_to_supplier(make, session, client, monkeypatch):
    from app.modules.account import mail

    captured: dict = {}
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(
        mail, "send_email", lambda **kwargs: captured.update(kwargs) or "msg-id"
    )

    admin, _ = _admin(make, client)
    _, partner = _partner(make, client, admin)
    drama = _submitted_drama(session, client, partner)

    r = client.post(
        f"/admin/v1/reviews/{drama['id']}/decision",
        json={"decision": "reject", "note": "封面尺寸不足"},
        headers=admin,
    )
    assert r.status_code == 200
    assert captured["to"] == "partner@example.com"
    assert "已驳回" in captured["subject"]
    assert "封面尺寸不足" in captured["text"]
