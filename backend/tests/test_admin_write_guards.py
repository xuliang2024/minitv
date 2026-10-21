"""Write-path guards used by the CLI: duplicate titles and episode deletion.

Both exist so a machine caller can distinguish "nothing happened" from "it
happened and you did not hear back": a 409 with the existing id makes
`drama create --upsert` safe to retry, and the empty-shell rule keeps a delete
from silently dropping media that viewers may still hold.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(session):
    return TestClient(app)


def _headers(make, client, role="admin") -> dict:
    account = make.admin(role=role)
    response = client.post(
        "/admin/v1/auth/login", json={"username": account.username, "password": "pass-1234"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_duplicate_title_returns_409_with_id(make, client):
    headers = _headers(make, client)
    first = client.post("/admin/v1/dramas", json={"title": "Same Name"}, headers=headers)
    assert first.status_code == 200
    drama_id = first.json()["id"]

    second = client.post("/admin/v1/dramas", json={"title": "Same Name"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "title_exists"
    assert second.json()["detail"]["drama_id"] == drama_id


def test_rename_onto_existing_title_returns_409(make, client):
    headers = _headers(make, client)
    client.post("/admin/v1/dramas", json={"title": "Taken"}, headers=headers)
    other = client.post("/admin/v1/dramas", json={"title": "Other"}, headers=headers).json()

    response = client.patch(
        f"/admin/v1/dramas/{other['id']}", json={"title": "Taken"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "title_exists"


def test_delete_episode_rejects_non_empty(make, client):
    headers = _headers(make, client)
    drama = make.drama(title="With media")
    episode = make.episode(drama, number=1, status="in_review")

    response = client.delete(f"/admin/v1/episodes/{episode.id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "episode_not_empty"


def test_delete_empty_shell(make, client, session):
    from app.db import get_engine, make_session_factory
    from app.modules.content.models import Episode

    headers = _headers(make, client)
    drama = make.drama(title="Shell only")
    episode = make.episode(drama, number=3, status="not_uploaded")

    response = client.delete(f"/admin/v1/episodes/{episode.id}", headers=headers)
    assert response.status_code == 204
    # The API deleted it in its own session; check with a clean one so the
    # test session's identity map cannot answer from cache.
    probe = make_session_factory(get_engine())()
    try:
        assert probe.get(Episode, episode.id) is None
    finally:
        probe.close()


def test_delete_episode_requires_write(make, client):
    drama = make.drama(title="No touch")
    episode = make.episode(drama, number=1, status="not_uploaded")
    headers = _headers(make, client, role="finance")

    assert client.delete(f"/admin/v1/episodes/{episode.id}", headers=headers).status_code == 403
