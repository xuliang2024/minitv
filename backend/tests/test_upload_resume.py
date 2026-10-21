"""Resume path: re-minting TUS headers for an upload that is already running.

The signature expires after an hour; without this endpoint an interrupted
upload can only restart from byte 0 because ``upload-session`` refuses to run
while the episode is ``transcoding``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(session):
    return TestClient(app)


def _headers(make, client) -> dict:
    account = make.admin(role="admin")
    response = client.post(
        "/admin/v1/auth/login", json={"username": account.username, "password": "pass-1234"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _episode_in_transcoding(client, headers, make, session):
    drama = make.drama(title="Resume target")
    episode = make.episode(drama, number=1)
    started = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={
            "filename": "ep1.mp4",
            "mime": "video/mp4",
            "size_bytes": 1024,
            "width": 0,
            "height": 0,
            "duration_seconds": 3,
        },
        headers=headers,
    )
    assert started.status_code == 200, started.text
    session.expire_all()
    return drama, episode, started.json()


def test_upload_headers_mint_and_resume(make, client, session):
    headers = _headers(make, client)
    drama, episode, started = _episode_in_transcoding(client, headers, make, session)

    minted = client.post(f"/admin/v1/episodes/{episode.id}/upload-headers", headers=headers)
    assert minted.status_code == 200, minted.text
    payload = minted.json()
    assert payload["video_id"] == started["video_id"]
    assert payload["upload_url"].startswith("/admin/v1/dev-tus/")
    assert "AuthorizationSignature" in payload["headers"]

    # HEAD tells the client how many bytes are already stored (none yet).
    head = client.head(f"/admin/v1/dev-tus/{payload['video_id']}", headers=headers)
    assert head.status_code == 200
    assert head.headers["Upload-Offset"] == "0"

    # Completing the upload flips the episode, after which resuming is moot.
    patched = client.patch(
        f"/admin/v1/dev-tus/{payload['video_id']}",
        content=b"x" * 1024,
        headers={**headers, "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"},
    )
    assert patched.status_code in (200, 204), patched.text

    again = client.post(f"/admin/v1/episodes/{episode.id}/upload-headers", headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "upload_not_resumable"


def test_upload_headers_requires_transcoding(make, client):
    headers = _headers(make, client)
    drama = make.drama(title="Not uploading")
    episode = make.episode(drama, number=1, status="not_uploaded")

    response = client.post(f"/admin/v1/episodes/{episode.id}/upload-headers", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "upload_not_resumable"
