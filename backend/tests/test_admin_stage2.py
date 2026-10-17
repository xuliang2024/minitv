"""Stage-2 acceptance: admin auth, roles, drama CRUD, tag library, public invisibility."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client: TestClient, username: str, password: str = "pass-1234") -> dict:
    r = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _ops_headers(make, client) -> dict:
    ops = make.admin(role="ops")
    return _login(client, ops.username)


# ---- acceptance 1: 401 without login; finance cannot write content ----


def test_unauthenticated_write_returns_401(make, session, client):
    r = client.post("/admin/v1/dramas", json={"title": "X"})
    assert r.status_code == 401
    r = client.patch("/admin/v1/dramas/1", json={"title": "Y"})
    assert r.status_code == 401
    r = client.delete("/admin/v1/tags/1")
    assert r.status_code == 401
    r = client.get("/admin/v1/dramas")
    assert r.status_code == 401


def test_bad_token_returns_401(make, session, client):
    r = client.get("/admin/v1/dramas", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_finance_role_cannot_write_content(make, session, client):
    finance = make.admin(role="finance")
    headers = _login(client, finance.username)

    r = client.post("/admin/v1/dramas", json={"title": "X"}, headers=headers)
    assert r.status_code == 403
    assert "cannot write content" in r.json()["detail"]

    # finance may read
    r = client.get("/admin/v1/dramas", headers=headers)
    assert r.status_code == 200


def test_login_rejects_wrong_password(make, session, client):
    make.admin(role="ops", username="ops-alice")
    r = client.post("/admin/v1/auth/login", json={"username": "ops-alice", "password": "wrong"})
    assert r.status_code == 401


# ---- acceptance 2: new drama defaults to draft and stays invisible on /v1 ----


def test_new_drama_is_draft_and_invisible_publicly(make, session, client):
    headers = _ops_headers(make, client)
    make.drama(title="Already Public", status="published")

    r = client.post("/admin/v1/dramas", json={"title": "Hidden Draft"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft"
    drama_id = body["id"]

    # /v1 home does not contain it
    home_items = client.get("/v1/home").json()["items"]
    assert all(item["id"] != drama_id for item in home_items)
    assert any(item["title"] == "Already Public" for item in home_items)

    # /v1 detail 404s
    assert client.get(f"/v1/dramas/{drama_id}").status_code == 404

    # /v1 search misses it
    search_items = client.get("/v1/search", params={"q": "Hidden"}).json()["items"]
    assert all(item["id"] != drama_id for item in search_items)

    # admin search finds it (back office sees drafts)
    admin_hits = client.get("/admin/v1/search", params={"q": "Hidden"}, headers=headers).json()
    assert any(d["id"] == drama_id for d in admin_hits["dramas"])
    assert admin_hits["users"] == []


def test_list_dramas_status_counts_cover_all_statuses(make, session, client):
    """顶部统计条依赖 status_counts：全库按状态计数，且不受分页和筛选影响。"""
    headers = _ops_headers(make, client)
    make.drama(title="Pub One", status="published")
    make.drama(title="Pub Two", status="published")
    make.drama(title="Reviewing", status="in_review")
    make.drama(title="Shelved", status="unpublished")
    # 默认新建就是草稿
    client.post("/admin/v1/dramas", json={"title": "Fresh Draft"}, headers=headers)

    body = client.get("/admin/v1/dramas", params={"page_size": 1}, headers=headers).json()
    assert body["status_counts"] == {
        "all": 5,
        "draft": 1,
        "in_review": 1,
        "published": 2,
        "unpublished": 1,
    }
    # 只返回一页，但计数仍是全库的
    assert len(body["items"]) == 1

    filtered = client.get(
        "/admin/v1/dramas", params={"status": "published"}, headers=headers
    ).json()
    assert filtered["total"] == 2
    assert filtered["status_counts"]["all"] == 5
    assert filtered["status_counts"]["published"] == 2


# ---- acceptance 3: saved basic info reads back with new values ----


def test_basic_info_roundtrip(make, session, client):
    headers = _ops_headers(make, client)
    tag = client.post(
        "/admin/v1/tags",
        json={"name_en": "Urban Fantasy", "name_zh": "都市奇幻", "category": "genre"},
        headers=headers,
    ).json()

    drama_id = client.post("/admin/v1/dramas", json={"title": "Draft A"}, headers=headers).json()["id"]

    r = client.patch(
        f"/admin/v1/dramas/{drama_id}",
        json={
            "title": "Renamed",
            "synopsis": "a fresh synopsis",
            "cover_url": "https://cdn.example/cover.jpg",
            "free_episode_count": 5,
            "coin_price_per_episode": 80,
            "tag_ids": [tag["id"]],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    got = client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()
    assert got["title"] == "Renamed"
    assert got["synopsis"] == "a fresh synopsis"
    assert got["cover_url"] == "https://cdn.example/cover.jpg"
    assert got["free_episode_count"] == 5
    assert got["coin_price_per_episode"] == 80
    assert [t["id"] for t in got["tags"]] == [tag["id"]]


# ---- regression: duplicate tag ids in PATCH must be tolerated, not 500 ----


def test_update_drama_tolerates_duplicate_tag_ids(make, session, client):
    headers = _ops_headers(make, client)
    tag = client.post(
        "/admin/v1/tags",
        json={"name_en": "Duplicate Safe", "category": "genre"},
        headers=headers,
    ).json()
    drama_id = client.post("/admin/v1/dramas", json={"title": "Dup Tag Drama"}, headers=headers).json()["id"]

    r = client.patch(
        f"/admin/v1/dramas/{drama_id}",
        json={"tag_ids": [tag["id"], tag["id"]]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()
    assert [t["id"] for t in got["tags"]] == [tag["id"]]


# ---- acceptance 4: a tag without an English name cannot be saved ----


def test_tag_requires_english_name(make, session, client):
    headers = _ops_headers(make, client)

    r = client.post("/admin/v1/tags", json={"name_zh": "只有中文"}, headers=headers)
    assert r.status_code == 422

    r = client.post("/admin/v1/tags", json={"name_en": ""}, headers=headers)
    assert r.status_code == 422

    # with a proper English name it saves
    r = client.post("/admin/v1/tags", json={"name_en": "Romance", "name_zh": "甜宠"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["name_en"] == "Romance"

    # duplicate English name rejected
    r = client.post("/admin/v1/tags", json={"name_en": "Romance"}, headers=headers)
    assert r.status_code == 409


# ---- acceptance 5: server-side filtering and pagination ----


def test_server_side_filter_and_pagination(make, session, client):
    headers = _ops_headers(make, client)
    for i in range(12):
        make.drama(title=f"Pub-{i:02d}", status="published")
    for i in range(3):
        make.drama(title=f"Dr-{i:02d}", status="draft")

    # status filter
    r = client.get("/admin/v1/dramas", params={"status": "draft"}, headers=headers).json()
    assert r["total"] == 3
    assert all(item["status"] == "draft" for item in r["items"])

    # q filter
    r = client.get("/admin/v1/dramas", params={"q": "Pub-"}, headers=headers).json()
    assert r["total"] == 12

    # combined filter + pagination: 12 hits, page_size 5 -> 5/5/2
    page1 = client.get(
        "/admin/v1/dramas", params={"q": "Pub-", "page": 1, "page_size": 5}, headers=headers
    ).json()
    assert page1["total"] == 12
    assert len(page1["items"]) == 5

    page3 = client.get(
        "/admin/v1/dramas", params={"q": "Pub-", "page": 3, "page_size": 5}, headers=headers
    ).json()
    assert len(page3["items"]) == 2

    ids1 = {item["id"] for item in page1["items"]}
    ids3 = {item["id"] for item in page3["items"]}
    assert ids1.isdisjoint(ids3)  # genuinely server-side paging, not one repeated page

    # tag filter
    tag = client.post("/admin/v1/tags", json={"name_en": "T-Paged"}, headers=headers).json()
    client.patch(f"/admin/v1/dramas/{page1['items'][0]['id']}", json={"tag_ids": [tag["id"]]}, headers=headers)
    r = client.get("/admin/v1/dramas", params={"tag_id": tag["id"]}, headers=headers).json()
    assert r["total"] == 1


# ---- tag deletion returns usage impact ----


def test_tag_delete_returns_usage_impact(make, session, client):
    headers = _ops_headers(make, client)
    tag = client.post("/admin/v1/tags", json={"name_en": "In-Use"}, headers=headers).json()
    drama_id = client.post("/admin/v1/dramas", json={"title": "Tagged Drama"}, headers=headers).json()["id"]
    client.patch(f"/admin/v1/dramas/{drama_id}", json={"tag_ids": [tag["id"]]}, headers=headers)

    r = client.delete(f"/admin/v1/tags/{tag['id']}", headers=headers)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["affected_dramas"] == [{"id": drama_id, "title": "Tagged Drama"}]

    # unused tag deletes cleanly
    free_tag = client.post("/admin/v1/tags", json={"name_en": "Free"}, headers=headers).json()
    r = client.delete(f"/admin/v1/tags/{free_tag['id']}", headers=headers)
    assert r.status_code == 200


# ---- logout invalidates the session ----


def test_logout_invalidates_session(make, session, client):
    ops = make.admin(role="ops")
    headers = _login(client, ops.username)
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 200
    client.post("/admin/v1/auth/logout", headers=headers)
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 401


# ---- cover upload / replace / delete ----


JPEG = b"\xff\xd8\xff" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WEBP = b"RIFF" + b"\x18\x00\x00\x00" + b"WEBP" + b"\x00" * 8


def _stub_bunny_storage(monkeypatch):
    """Real mode without touching the network or the dev storage zone."""
    from app.modules.common import bunny

    monkeypatch.setenv("BUNNY_MODE", "real")
    stored: dict[str, dict] = {}
    deleted: list[str] = []
    purged: list[str] = []

    def fake_upload(remote_path, data, content_type="application/octet-stream"):
        stored[remote_path] = {"data": data, "content_type": content_type}

    def fake_delete(remote_path):
        deleted.append(remote_path)
        stored.pop(remote_path, None)

    monkeypatch.setattr(bunny, "storage_upload", fake_upload)
    monkeypatch.setattr(bunny, "storage_delete", fake_delete)
    monkeypatch.setattr(bunny, "purge_url", purged.append)
    monkeypatch.setattr(bunny, "image_url", lambda remote_path: f"https://img.test/{remote_path}")
    return stored, deleted, purged


def test_cover_upload_requires_login_and_writer(make, session, client):
    assert client.post("/admin/v1/dramas/1/cover", files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 401
    assert client.delete("/admin/v1/dramas/1/cover").status_code == 401

    finance = make.admin(role="finance")
    headers = _login(client, finance.username)
    assert client.post(
        "/admin/v1/dramas/1/cover",
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
        headers=headers,
    ).status_code == 403
    assert client.delete("/admin/v1/dramas/1/cover", headers=headers).status_code == 403


def test_cover_upload_rejected_without_storage(make, session, client, monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "fake")
    headers = _ops_headers(make, client)
    drama_id = client.post("/admin/v1/dramas", json={"title": "No Storage"}, headers=headers).json()["id"]
    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 503
    assert "链接" in r.json()["detail"]


def test_cover_upload_replace_and_delete(make, session, client, monkeypatch):
    stored, deleted, purged = _stub_bunny_storage(monkeypatch)
    headers = _ops_headers(make, client)
    drama_id = client.post("/admin/v1/dramas", json={"title": "Cover"}, headers=headers).json()["id"]

    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("poster.png", PNG, "image/png")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    first = r.json()["cover_url"]
    assert first.startswith(f"https://img.test/covers/{drama_id}/cover.png?v=")
    assert stored[f"covers/{drama_id}/cover.png"]["content_type"] == "image/png"
    assert client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()["cover_url"] == first

    # Bytes win over a lying Content-Type. The previous png object is removed.
    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("poster.gif", JPEG, "image/gif")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    second = r.json()["cover_url"]
    assert second.startswith(f"https://img.test/covers/{drama_id}/cover.jpg?v=")
    assert f"covers/{drama_id}/cover.png" not in stored
    assert f"covers/{drama_id}/cover.png" in deleted
    assert stored[f"covers/{drama_id}/cover.jpg"]["content_type"] == "image/jpeg"
    assert any(url.startswith(f"https://img.test/covers/{drama_id}/cover.jpg") for url in purged)

    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("poster.webp", WEBP, "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["cover_url"].startswith(f"https://img.test/covers/{drama_id}/cover.webp?v=")

    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("nope.gif", b"GIF89a" + b"\x00" * 8, "image/gif")},
        headers=headers,
    )
    assert r.status_code == 422
    assert "JPG" in r.json()["detail"]

    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 422

    import app.modules.ops.flow_router as flow

    monkeypatch.setattr(flow, "COVER_MAX_BYTES", 8)
    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("big.jpg", JPEG + b"x" * 32, "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 422
    assert "5MB" in r.json()["detail"]

    from app.modules.common import bunny

    # The size-limit override above must not leak into the upstream-failure case.
    monkeypatch.setattr(flow, "COVER_MAX_BYTES", 5 * 1024 * 1024)

    def boom(*_a, **_k):
        raise bunny.BunnyError("upstream down")

    monkeypatch.setattr(bunny, "storage_upload", boom)
    before = client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()["cover_url"]
    r = client.post(
        f"/admin/v1/dramas/{drama_id}/cover",
        files={"file": ("again.png", PNG, "image/png")},
        headers=headers,
    )
    assert r.status_code == 502
    assert client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()["cover_url"] == before

    r = client.delete(f"/admin/v1/dramas/{drama_id}/cover", headers=headers)
    assert r.status_code == 200
    assert r.json()["cover_url"] == ""
    assert client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()["cover_url"] == ""
    assert client.delete("/admin/v1/dramas/999999/cover", headers=headers).status_code == 404


def test_cover_delete_in_fake_mode_clears_url_only(make, session, client, monkeypatch):
    from app.modules.common import bunny

    monkeypatch.setenv("BUNNY_MODE", "fake")

    def explode(*_a, **_k):
        raise AssertionError("fake mode must not touch storage")

    monkeypatch.setattr(bunny, "storage_delete", explode)
    monkeypatch.setattr(bunny, "purge_url", explode)
    headers = _ops_headers(make, client)
    drama_id = client.post("/admin/v1/dramas", json={"title": "Linked"}, headers=headers).json()["id"]
    client.patch(
        f"/admin/v1/dramas/{drama_id}",
        json={"cover_url": "https://cdn.example/cover.jpg"},
        headers=headers,
    )
    r = client.delete(f"/admin/v1/dramas/{drama_id}/cover", headers=headers)
    assert r.status_code == 200
    assert client.get(f"/admin/v1/dramas/{drama_id}", headers=headers).json()["cover_url"] == ""
