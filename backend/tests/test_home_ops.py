"""Home-ops editor: pinned Discover tab, banner carousel, floor style."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _ops(make, client):
    return _login(client, make.admin(role="ops").username)


def test_discover_stays_pinned_and_other_tabs_bind_a_group(make, client):
    headers = _ops(make, client)
    group = client.post("/admin/v1/groups", json={"name": "榜单", "position": 1}, headers=headers).json()["id"]

    discover = client.post(
        "/admin/v1/tabs",
        json={"code": "discover", "title_en": "Discover", "title_zh": "发现", "position": 0},
        headers=headers,
    )
    assert discover.status_code == 200, discover.text
    assert discover.json()["locked"] is True

    ranking = client.post(
        "/admin/v1/tabs",
        json={"code": "ranking", "title_en": "Ranking", "title_zh": "榜单", "position": 1, "group_id": group},
        headers=headers,
    )
    assert ranking.status_code == 200, ranking.text
    ranking_id = ranking.json()["id"]

    blocked = client.patch(f"/admin/v1/tabs/{discover.json()['id']}", json={"position": 4}, headers=headers)
    assert blocked.status_code == 409

    renamed = client.patch(
        f"/admin/v1/tabs/{discover.json()['id']}", json={"title_zh": "发现页"}, headers=headers
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title_zh"] == "发现页"
    assert renamed.json()["position"] == 0

    rebound = client.patch(f"/admin/v1/tabs/{ranking_id}", json={"group_id": group, "enabled": False}, headers=headers)
    assert rebound.status_code == 200, rebound.text
    assert rebound.json()["enabled"] is False

    listed = client.get("/admin/v1/tabs", headers=headers).json()["items"]
    assert [item["code"] for item in listed] == ["discover", "ranking"]

    removed = client.delete(f"/admin/v1/tabs/{discover.json()['id']}", headers=headers)
    assert removed.status_code == 409
    gone = client.delete(f"/admin/v1/tabs/{ranking_id}", headers=headers)
    assert gone.status_code == 200


def test_banner_keeps_copy_and_stops_at_three(make, client):
    headers = _ops(make, client)
    dramas = [make.drama(title=f"Banner {i}", status="published") for i in range(4)]

    first = client.post(
        "/admin/v1/banners",
        json={"drama_id": dramas[0].id, "kicker": "穿成黑帮老大后", "headline": "我把自己睡了", "position": 0},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["kicker"] == "穿成黑帮老大后"

    for index, drama in enumerate(dramas[1:3], start=1):
        added = client.post("/admin/v1/banners", json={"drama_id": drama.id, "position": index}, headers=headers)
        assert added.status_code == 200, added.text

    overflow = client.post("/admin/v1/banners", json={"drama_id": dramas[3].id}, headers=headers)
    assert overflow.status_code == 409

    edited = client.patch(f"/admin/v1/banners/{first.json()['id']}", json={"headline": "新标题"}, headers=headers)
    assert edited.json()["headline"] == "新标题"

    public = client.get("/v1/home/groups").json()["banners"]
    assert [item["headline"] for item in public][0] == "新标题"
    assert public[0]["title"] == "Banner 0"

    deleted = client.delete(f"/admin/v1/banners/{first.json()['id']}", headers=headers)
    assert deleted.status_code == 200


def test_floor_style_is_saved_and_published(make, client):
    headers = _ops(make, client)
    drama = make.drama(title="Ranked", status="published")
    created = client.post("/admin/v1/groups", json={"name": "必看榜", "position": 0}, headers=headers)
    group_id = created.json()["id"]
    client.post(f"/admin/v1/groups/{group_id}/dramas", json={"drama_id": drama.id, "confirm": True}, headers=headers)

    styled = client.patch(
        f"/admin/v1/groups/{group_id}", json={"style": "rank", "badge": "label"}, headers=headers
    )
    assert styled.status_code == 200, styled.text
    assert styled.json()["style"] == "rank"
    assert styled.json()["badge"] == "label"

    rejected = client.patch(f"/admin/v1/groups/{group_id}", json={"style": "carousel"}, headers=headers)
    assert rejected.status_code == 422

    public = client.get("/v1/home/groups").json()["groups"]
    floor = next(item for item in public if item["id"] == group_id)
    assert floor["style"] == "rank"
    assert floor["badge"] == "label"
    assert floor["dramas"][0]["title"] == "Ranked"
