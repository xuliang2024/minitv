"""API key lifecycle and the key→account permission bridge.

The security-critical assertions are the coupling ones: disabling the bound
account, revoking the key and narrowing scopes must all take effect on the
*next* request, with no restart and no session involved.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.common.models import AuditLog
from app.modules.ops.models import AdminApiKey, ApiKeyEvent


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client: TestClient, username: str, password: str = "pass-1234") -> dict:
    r = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin_headers(make, client) -> tuple[dict, object]:
    account = make.admin(role="admin")
    return _login(client, account.username), account


def _issue(client, headers, user_id: int, **overrides) -> dict:
    body = {
        "user_id": user_id,
        "name": "agent-ops/upload-bot",
        "scopes": ["read", "write"],
        "expires_in_days": 90,
    }
    body.update(overrides)
    r = client.post("/admin/v1/api-keys", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _key_headers(payload: dict) -> dict:
    return {"Authorization": f"Bearer {payload['key']}"}


# ---- issue / list ----


def test_create_returns_plaintext_but_list_never_does(make, client, session):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    assert created["key"].startswith("mk_live_")
    assert created["key_prefix"] == created["key"][:16]
    assert created["state"] == "active"
    assert created["username"] == "agent-ops"

    listed = client.get("/admin/v1/api-keys", headers=headers).json()
    row = listed["items"][0]
    assert "key" not in row
    assert "key_hash" not in row
    assert row["key_prefix"] == created["key_prefix"]
    assert listed["summary"]["active"] == 1

    stored = session.get(AdminApiKey, created["id"])
    assert stored.key_hash != created["key"]
    assert len(stored.key_hash) == 64


def test_key_is_bound_to_its_account(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    me = client.get("/admin/v1/auth/me", headers=_key_headers(created)).json()
    assert me["username"] == "agent-ops"
    assert me["role"] == "ops"


def test_only_admin_can_manage_keys(make, client):
    ops = make.admin(role="ops")
    headers = _login(client, ops.username)

    assert client.get("/admin/v1/api-keys", headers=headers).status_code == 403
    assert (
        client.post(
            "/admin/v1/api-keys",
            json={"user_id": ops.id, "name": "self", "scopes": ["read"]},
            headers=headers,
        ).status_code
        == 403
    )
    # Self-service read of one's own keys stays available.
    own = client.get("/admin/v1/auth/keys", headers=headers)
    assert own.status_code == 200


def test_duplicate_name_conflicts(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    _issue(client, headers, ops.id)

    r = client.post(
        "/admin/v1/api-keys",
        json={"user_id": ops.id, "name": "agent-ops/upload-bot", "scopes": ["read"]},
        headers=headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "key_name_conflict"


def test_unknown_scope_rejected(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    r = client.post(
        "/admin/v1/api-keys",
        json={"user_id": ops.id, "name": "bad", "scopes": ["read", "superuser"]},
        headers=headers,
    )
    assert r.status_code == 422


# ---- authentication failures ----


def test_missing_and_invalid_credentials(make, client):
    missing = client.get("/admin/v1/dramas")
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "missing_credential"

    bogus = client.get(
        "/admin/v1/dramas", headers={"Authorization": "Bearer mk_live_deadbeef_nope"}
    )
    assert bogus.status_code == 401
    assert bogus.json()["detail"]["code"] == "invalid_key"


def test_expired_key_is_rejected(make, client, session):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    row = session.get(AdminApiKey, created["id"])
    row.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=1
    )
    session.commit()

    r = client.get("/admin/v1/dramas", headers=_key_headers(created))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "key_expired"


def test_revoked_key_is_rejected(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    assert (
        client.post(
            f"/admin/v1/api-keys/{created['id']}/revoke",
            json={"reason": "done"},
            headers=headers,
        ).status_code
        == 200
    )
    r = client.get("/admin/v1/dramas", headers=_key_headers(created))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "key_revoked"
    # Revoking twice is idempotent.
    again = client.post(f"/admin/v1/api-keys/{created['id']}/revoke", json={}, headers=headers)
    assert again.status_code == 200


def test_disabling_the_account_kills_the_key(make, client, session):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    assert client.get("/admin/v1/dramas", headers=_key_headers(created)).status_code == 200

    ops.status = "disabled"
    session.commit()

    r = client.get("/admin/v1/dramas", headers=_key_headers(created))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "account_disabled"


# ---- scope enforcement ----


def test_read_only_key_cannot_write(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id, scopes=["read"])

    assert client.get("/admin/v1/dramas", headers=_key_headers(created)).status_code == 200
    r = client.post("/admin/v1/dramas", json={"title": "Blocked"}, headers=_key_headers(created))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "scope_denied"


def test_write_key_cannot_delete(make, client, session):
    headers, _ = _admin_headers(make, client)
    boss = make.admin(role="admin", username="boss")
    created = _issue(client, headers, boss.id, scopes=["read", "write"])

    drama = make.drama(title="Delete me")
    r = client.post(
        f"/admin/v1/dramas/{drama.id}/delete",
        json={"confirm": True},
        headers=_key_headers(created),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "scope_denied"


def test_delete_scope_allows_deletion(make, client):
    headers, _ = _admin_headers(make, client)
    boss = make.admin(role="admin", username="boss")
    created = _issue(client, headers, boss.id, scopes=["read", "write", "delete"])

    drama = make.drama(title="Gone")
    r = client.post(
        f"/admin/v1/dramas/{drama.id}/delete",
        json={"confirm": True},
        headers=_key_headers(created),
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True


def test_write_key_cannot_reach_staff_or_billing(make, client):
    headers, _ = _admin_headers(make, client)
    boss = make.admin(role="admin", username="boss")
    created = _issue(client, headers, boss.id, scopes=["read", "write"])

    assert client.get("/admin/v1/staff", headers=_key_headers(created)).status_code == 403
    assert client.get("/admin/v1/finance/orders", headers=_key_headers(created)).status_code == 403
    # ...while the admin session itself still can.
    assert client.get("/admin/v1/staff", headers=headers).status_code == 200


def test_key_cannot_change_its_owners_password(make, client):
    headers, _ = _admin_headers(make, client)
    boss = make.admin(role="admin", username="boss")
    created = _issue(client, headers, boss.id, scopes=["read", "write"])

    r = client.post(
        "/admin/v1/auth/password",
        json={"old_password": "pass-1234", "new_password": "hijacked-1234"},
        headers=_key_headers(created),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "scope_denied"


# ---- lifecycle ----


def test_rotate_invalidates_the_old_key(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    rotated = client.post(
        f"/admin/v1/api-keys/{created['id']}/rotate", json={}, headers=headers
    )
    assert rotated.status_code == 201, rotated.text
    fresh = rotated.json()
    assert fresh["key"] != created["key"]
    assert fresh["name"] == created["name"]

    assert client.get("/admin/v1/dramas", headers=_key_headers(created)).status_code == 401
    assert client.get("/admin/v1/dramas", headers=_key_headers(fresh)).status_code == 200

    listed = client.get("/admin/v1/api-keys", headers=headers).json()
    assert listed["summary"]["active"] == 1
    assert listed["summary"]["revoked"] == 1


def test_patch_narrows_scopes(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id, scopes=["read", "write"])

    r = client.patch(
        f"/admin/v1/api-keys/{created['id']}", json={"scopes": ["read"]}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == ["read"]

    denied = client.post("/admin/v1/dramas", json={"title": "Nope"}, headers=_key_headers(created))
    assert denied.status_code == 403


def test_delete_only_unused_keys(make, client):
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    used = _issue(client, headers, ops.id)
    unused = _issue(client, headers, ops.id, name="never-used")

    assert client.get("/admin/v1/dramas", headers=_key_headers(used)).status_code == 200

    blocked = client.delete(f"/admin/v1/api-keys/{used['id']}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "key_in_use"

    assert client.delete(f"/admin/v1/api-keys/{unused['id']}", headers=headers).status_code == 204


# ---- telemetry ----


def test_audit_and_events(make, client, session):
    headers, actor = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    actions = {
        row.action
        for row in session.query(AuditLog)
        .filter(AuditLog.target_type == "api_key")
        .all()
    }
    assert "api_key_create" in actions

    r = client.post(
        "/admin/v1/dramas", json={"title": "From agent"}, headers=_key_headers(created)
    )
    assert r.status_code == 200, r.text

    events = session.query(ApiKeyEvent).all()
    assert len(events) == 1
    assert events[0].key_id == created["id"]
    assert events[0].method == "POST"
    assert events[0].path == "/admin/v1/dramas"
    assert events[0].status_code == 200

    # GETs are deliberately not recorded.
    client.get("/admin/v1/dramas", headers=_key_headers(created))
    assert session.query(ApiKeyEvent).count() == 1

    listing = client.get(
        f"/admin/v1/api-keys/{created['id']}/events", headers=headers
    ).json()
    assert len(listing["items"]) == 1


# ---- rate limit & regressions ----


def test_rate_limit(make, client, monkeypatch):
    monkeypatch.setenv("API_KEY_RATE_LIMIT", "3")
    headers, _ = _admin_headers(make, client)
    ops = make.admin(role="ops", username="agent-ops")
    created = _issue(client, headers, ops.id)

    codes = [
        client.get("/admin/v1/dramas", headers=_key_headers(created)).status_code
        for _ in range(4)
    ]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_session_auth_is_unchanged(make, client):
    headers, _ = _admin_headers(make, client)
    assert client.get("/admin/v1/dramas", headers=headers).status_code == 200
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 200

    bad = client.get("/admin/v1/dramas", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "invalid_session"
