"""P2: partner write surface (drama / episodes / upload / submit) and API keys."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.content.models import Drama, Episode, Tag


@pytest.fixture()
def client(session):
    return TestClient(app)


def _admin_headers(make, client) -> dict:
    admin = make.admin(role="admin")
    r = client.post(
        "/admin/v1/auth/login", json={"username": admin.username, "password": "pass-1234"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _supplier_with_partner(client, headers, name="星野传媒", username="sp-a") -> tuple[dict, dict]:
    sup = client.post(
        "/admin/v1/suppliers",
        json={"name": name, "contact_email": "s@example.com"},
        headers=headers,
    )
    assert sup.status_code == 201, sup.text
    body = sup.json()
    acc = client.post(
        "/admin/v1/staff",
        json={
            "username": username,
            "display_name": name,
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": body["id"],
        },
        headers=headers,
    )
    assert acc.status_code == 201, acc.text
    login = client.post(
        "/admin/v1/auth/login", json={"username": username, "password": "partner-pass-1234"}
    )
    assert login.status_code == 200, login.text
    return body, {"Authorization": f"Bearer {login.json()['token']}"}


def _create_drama(client, partner, title="我的剧", **extra) -> dict:
    r = client.post("/partner/v1/dramas", json={"title": title, **extra}, headers=partner)
    assert r.status_code == 201, r.text
    return r.json()


# ---- drama CRUD ----


def test_create_drama_defaults(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)

    assert drama["free_episode_count"] == 5
    assert drama["status"] == "draft" and drama["review_state"] == "none"
    assert drama["episode_count"] == 0 and drama["editable"] is True
    row = session.get(Drama, drama["id"])
    assert row.coin_price_per_episode == 0
    assert row.supplier_id is not None


def test_duplicate_title_is_per_supplier(make, session, client):
    headers = _admin_headers(make, client)
    sup_a, partner_a = _supplier_with_partner(client, headers, name="甲", username="sp-a")
    sup_b, partner_b = _supplier_with_partner(client, headers, name="乙", username="sp-b")

    _create_drama(client, partner_a, title="替身新娘")
    dup = client.post("/partner/v1/dramas", json={"title": "替身新娘"}, headers=partner_a)
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "title_exists"

    other = client.post("/partner/v1/dramas", json={"title": "替身新娘"}, headers=partner_b)
    assert other.status_code == 201, other.text


def test_price_field_is_ignored(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    r = client.post(
        "/partner/v1/dramas",
        json={"title": "定价测试", "coin_price_per_episode": 99, "free_episode_count": 5},
        headers=partner,
    )
    assert r.status_code == 201
    assert session.get(Drama, r.json()["id"]).coin_price_per_episode == 0


def test_update_drama_and_tags(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)
    tag = Tag(name_en="Urban", name_zh="都市", category="main", status="enabled")
    session.add(tag)
    session.commit()

    r = client.patch(
        f"/partner/v1/dramas/{drama['id']}",
        json={"synopsis": "一段足够长的简介" * 4, "free_episode_count": 6, "tag_ids": [tag.id]},
        headers=partner,
    )
    assert r.status_code == 200, r.text
    assert r.json()["free_episode_count"] == 6
    assert r.json()["tag_ids"] == [tag.id]

    bad = client.patch(
        f"/partner/v1/dramas/{drama['id']}", json={"tag_ids": [99999]}, headers=partner
    )
    assert bad.status_code == 422


def test_published_drama_is_read_only(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)
    row = session.get(Drama, drama["id"])
    row.status = "in_review"
    session.commit()
    row.status = "published"
    session.commit()

    r = client.patch(
        f"/partner/v1/dramas/{drama['id']}", json={"synopsis": "改内容"}, headers=partner
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "drama_locked"


# ---- episodes ----


def test_episode_numbering_and_duplicate(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)

    first = client.post(f"/partner/v1/dramas/{drama['id']}/episodes", json={}, headers=partner)
    assert first.status_code == 201
    assert first.json()["episode_number"] == 1
    assert first.json()["title"] == "第 1 集"

    second = client.post(
        f"/partner/v1/dramas/{drama['id']}/episodes", json={"episode_number": 1}, headers=partner
    )
    assert second.status_code == 409


def test_delete_episode_only_without_video(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)
    ep = client.post(f"/partner/v1/dramas/{drama['id']}/episodes", json={}, headers=partner).json()

    assert client.delete(f"/partner/v1/episodes/{ep['id']}", headers=partner).status_code == 204

    ep2 = make.episode(session.get(Drama, drama["id"]), 2, status="in_review")
    r = client.delete(f"/partner/v1/episodes/{ep2.id}", headers=partner)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "episode_has_video"


def test_upload_session_is_scoped_to_owner(make, session, client):
    headers = _admin_headers(make, client)
    _, partner_a = _supplier_with_partner(client, headers, name="甲", username="sp-a")
    _, partner_b = _supplier_with_partner(client, headers, name="乙", username="sp-b")
    drama = _create_drama(client, partner_a)
    ep = client.post(f"/partner/v1/dramas/{drama['id']}/episodes", json={}, headers=partner_a).json()

    payload = {"filename": "ep1.mp4", "mime": "video/mp4", "size_bytes": 1024}
    assert (
        client.post(f"/partner/v1/episodes/{ep['id']}/upload-session", json=payload, headers=partner_b).status_code
        == 404
    )

    ok = client.post(
        f"/partner/v1/episodes/{ep['id']}/upload-session", json=payload, headers=partner_a
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "transcoding"

    status = client.get(f"/partner/v1/episodes/{ep['id']}/upload-status", headers=partner_a)
    assert status.status_code == 200
    assert status.json()["status"] == "transcoding"


# ---- submit ----


def _ready_drama(session, client, partner, *, episodes=5, free=5, synopsis=None) -> dict:
    drama = _create_drama(
        client,
        partner,
        synopsis=synopsis or ("一段完整的故事简介，" * 5),
        free_episode_count=free,
    )
    row = session.get(Drama, drama["id"])
    row.cover_url = "https://img.example.com/c.jpg"
    session.commit()
    for number in range(1, episodes + 1):
        make_episode = Episode(
            drama_id=row.id,
            episode_number=number,
            title=f"第 {number} 集",
            status="in_review",
        )
        session.add(make_episode)
    session.commit()
    return drama


def test_submit_requires_cover(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _ready_drama(session, client, partner)
    row = session.get(Drama, drama["id"])
    row.cover_url = ""
    session.commit()

    r = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "cover_required"


def test_submit_requires_synopsis(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _ready_drama(session, client, partner)
    row = session.get(Drama, drama["id"])
    row.synopsis = "太短"
    session.commit()

    r = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "synopsis_too_short"


def test_submit_requires_episodes(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner, synopsis="一段完整的故事简介，" * 5)
    row = session.get(Drama, drama["id"])
    row.cover_url = "https://img.example.com/c.jpg"
    session.commit()

    r = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "episodes_required"


def test_submit_requires_all_transcoded(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _ready_drama(session, client, partner, episodes=3, free=3)
    session.add(
        Episode(drama_id=drama["id"], episode_number=4, title="第 4 集", status="not_uploaded")
    )
    session.commit()

    r = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "episodes_not_ready"
    assert r.json()["detail"]["episodes"] == [4]


def test_submit_free_count_floor(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _ready_drama(session, client, partner, episodes=3, free=5)

    r = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "free_episode_count_out_of_range"
    assert r.json()["detail"]["floor"] == 3

    client.patch(
        f"/partner/v1/dramas/{drama['id']}", json={"free_episode_count": 3}, headers=partner
    )
    ok = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert ok.status_code == 200, ok.text
    assert ok.json()["review_state"] == "pending"
    assert ok.json()["submitted_at"] is not None


def test_submit_twice_conflicts(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _ready_drama(session, client, partner, episodes=5, free=5)
    assert client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner).status_code == 200
    second = client.post(f"/partner/v1/dramas/{drama['id']}/submit", headers=partner)
    assert second.status_code == 409


# ---- api keys ----


def test_partner_key_scopes_are_whitelisted(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)

    bad = client.post(
        "/partner/v1/api-keys", json={"name": "bad", "scopes": ["billing"]}, headers=partner
    )
    assert bad.status_code == 422

    ok = client.post(
        "/partner/v1/api-keys",
        json={"name": "ci", "scopes": ["read", "write", "upload"]},
        headers=partner,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["key"].startswith("mk_live_")
    assert ok.json()["scopes"] == ["read", "write", "upload"]


def test_partner_key_can_write_and_is_path_guarded(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    key = client.post(
        "/partner/v1/api-keys",
        json={"name": "ci", "scopes": ["read", "write", "upload"]},
        headers=partner,
    ).json()["key"]
    auth = {"Authorization": f"Bearer {key}"}

    created = client.post("/partner/v1/dramas", json={"title": "密钥建剧"}, headers=auth)
    assert created.status_code == 201, created.text

    admin = client.get("/admin/v1/dramas", headers=auth)
    assert admin.status_code == 403
    assert admin.json()["detail"]["code"] == "partner_scope_denied"


def test_read_only_key_cannot_write_or_upload(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    drama = _create_drama(client, partner)
    ep = client.post(f"/partner/v1/dramas/{drama['id']}/episodes", json={}, headers=partner).json()
    key = client.post(
        "/partner/v1/api-keys", json={"name": "ro", "scopes": ["read"]}, headers=partner
    ).json()["key"]
    auth = {"Authorization": f"Bearer {key}"}

    assert client.get("/partner/v1/dramas", headers=auth).status_code == 200
    write = client.post("/partner/v1/dramas", json={"title": "x"}, headers=auth)
    assert write.status_code == 403 and write.json()["detail"]["code"] == "scope_denied"
    payload = {"filename": "a.mp4", "mime": "video/mp4", "size_bytes": 10}
    up = client.post(f"/partner/v1/episodes/{ep['id']}/upload-session", json=payload, headers=auth)
    assert up.status_code == 403 and up.json()["detail"]["code"] == "scope_denied"


def test_key_cannot_manage_keys(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    key = client.post(
        "/partner/v1/api-keys", json={"name": "ci", "scopes": ["read", "write"]}, headers=partner
    ).json()["key"]
    auth = {"Authorization": f"Bearer {key}"}

    blocked = client.post("/partner/v1/api-keys", json={"name": "escalate"}, headers=auth)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "session_required"


def test_revoked_key_stops_working(make, session, client):
    headers = _admin_headers(make, client)
    _, partner = _supplier_with_partner(client, headers)
    issued = client.post(
        "/partner/v1/api-keys", json={"name": "ci", "scopes": ["read"]}, headers=partner
    ).json()
    auth = {"Authorization": f"Bearer {issued['key']}"}
    assert client.get("/partner/v1/me", headers=auth).status_code == 200

    revoked = client.post(
        f"/partner/v1/api-keys/{issued['id']}/revoke", json={"reason": "轮换"}, headers=partner
    )
    assert revoked.status_code == 200
    after = client.get("/partner/v1/me", headers=auth)
    assert after.status_code == 401
    assert after.json()["detail"]["code"] == "key_revoked"


def test_partner_cannot_revoke_another_suppliers_key(make, session, client):
    headers = _admin_headers(make, client)
    _, partner_a = _supplier_with_partner(client, headers, name="甲", username="sp-a")
    _, partner_b = _supplier_with_partner(client, headers, name="乙", username="sp-b")
    issued = client.post(
        "/partner/v1/api-keys", json={"name": "a", "scopes": ["read"]}, headers=partner_a
    ).json()

    r = client.post(f"/partner/v1/api-keys/{issued['id']}/revoke", headers=partner_b)
    assert r.status_code == 404
