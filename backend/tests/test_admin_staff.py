"""Staff account lifecycle: create / rename / disable / passwords.

The security-critical assertions here are the "immediately" ones: a disabled
account and a reset password must both invalidate *already issued* session
tokens, not just future logins.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.common.models import AuditLog


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


def _create(client, headers, **overrides) -> dict:
    body = {
        "username": "ops-lily",
        "display_name": "李莉",
        "role": "ops",
        "password": "lily-pass-1234",
    }
    body.update(overrides)
    r = client.post("/admin/v1/staff", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---- listing & permissions ----


def test_list_staff_omits_password_hash(make, session, client):
    headers, actor = _admin_headers(make, client)
    _create(client, headers)

    r = client.get("/admin/v1/staff", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert {row["username"] for row in items} == {actor.username, "ops-lily"}
    for row in items:
        assert "password_hash" not in row
        assert set(row) == {
            "id",
            "username",
            "display_name",
            "role",
            "status",
            "disable_reason",
            "supplier_id",
            "last_login_at",
            "created_at",
            "updated_at",
        }


@pytest.mark.parametrize("role", ["ops", "finance"])
def test_non_admin_cannot_touch_staff(make, session, client, role):
    staff = make.admin(role=role)
    headers = _login(client, staff.username)

    assert client.get("/admin/v1/staff", headers=headers).status_code == 403
    assert (
        client.post(
            "/admin/v1/staff",
            json={"username": "sneaky", "role": "admin", "password": "sneaky-pass-1234"},
            headers=headers,
        ).status_code
        == 403
    )


def test_list_filters(make, session, client):
    headers, actor = _admin_headers(make, client)
    _create(client, headers)

    assert len(client.get("/admin/v1/staff?role=ops", headers=headers).json()["items"]) == 1
    assert len(client.get("/admin/v1/staff?role=admin", headers=headers).json()["items"]) == 1
    assert len(client.get("/admin/v1/staff?status=disabled", headers=headers).json()["items"]) == 0
    found = client.get("/admin/v1/staff?q=lily", headers=headers).json()["items"]
    assert [row["username"] for row in found] == ["ops-lily"]


# ---- create ----


def test_created_account_can_log_in(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    assert created["status"] == "active"
    assert created["last_login_at"] is None

    _login(client, "ops-lily", "lily-pass-1234")
    listed = client.get("/admin/v1/staff?q=ops-lily", headers=headers).json()["items"][0]
    assert listed["last_login_at"] is not None


def test_duplicate_username_conflicts(make, session, client):
    headers, _ = _admin_headers(make, client)
    _create(client, headers)
    r = client.post(
        "/admin/v1/staff",
        json={"username": "ops-lily", "role": "ops", "password": "another-pass-1234"},
        headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.parametrize("username", ["ab", "lily space", "lily!", "_lily", "lily中文"])
def test_invalid_username_rejected(make, session, client, username):
    headers, _ = _admin_headers(make, client)
    r = client.post(
        "/admin/v1/staff",
        json={"username": username, "role": "ops", "password": "valid-pass-1234"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_username_is_normalised_to_lowercase(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers, username="Ops-Lily")
    assert created["username"] == "ops-lily"


def test_invalid_role_rejected(make, session, client):
    headers, _ = _admin_headers(make, client)
    r = client.post(
        "/admin/v1/staff",
        json={"username": "owner", "role": "owner", "password": "valid-pass-1234"},
        headers=headers,
    )
    assert r.status_code == 422


def test_weak_password_rejected(make, session, client):
    headers, _ = _admin_headers(make, client)
    short = client.post(
        "/admin/v1/staff",
        json={"username": "too-short", "role": "ops", "password": "short7!"},
        headers=headers,
    )
    assert short.status_code == 422

    same_as_username = client.post(
        "/admin/v1/staff",
        json={"username": "same-name", "role": "ops", "password": "same-name"},
        headers=headers,
    )
    assert same_as_username.status_code == 422


# ---- update ----


def test_rename_and_change_role(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)

    r = client.patch(
        f"/admin/v1/staff/{created['id']}",
        json={"display_name": "李莉（财务）", "role": "finance"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"] == {"display_name": "李莉", "role": "ops"}
    assert body["after"]["display_name"] == "李莉（财务）"
    assert body["after"]["role"] == "finance"

    # No-op patch stays a 200 and writes nothing.
    r = client.patch(f"/admin/v1/staff/{created['id']}", json={"role": "finance"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["before"] == {}


def test_cannot_change_own_role(make, session, client):
    headers, actor = _admin_headers(make, client)
    r = client.patch(f"/admin/v1/staff/{actor.id}", json={"role": "ops"}, headers=headers)
    assert r.status_code == 400
    assert "不能修改自己的角色" in r.json()["detail"]


# ---- disable / enable ----


def test_disable_kills_the_live_session(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    target = _login(client, "ops-lily", "lily-pass-1234")
    assert client.get("/admin/v1/staff", headers=target).status_code == 403  # ops cannot list
    assert client.get("/admin/v1/dramas", headers=target).status_code == 200

    r = client.post(
        f"/admin/v1/staff/{created['id']}/status",
        json={"status": "disabled", "reason": "离职"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disabled"
    assert r.json()["disable_reason"] == "离职"
    assert r.json()["revoked_sessions"] >= 1

    # Existing token is rejected on the very next request.
    assert client.get("/admin/v1/dramas", headers=target).status_code == 401
    # And the account cannot sign in again.
    r = client.post(
        "/admin/v1/auth/login", json={"username": "ops-lily", "password": "lily-pass-1234"}
    )
    assert r.status_code == 401

    listed = client.get("/admin/v1/staff?q=ops-lily", headers=headers).json()["items"][0]
    assert listed["status"] == "disabled"
    assert listed["disable_reason"] == "离职"


def test_enable_clears_disabled_state(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    client.post(
        f"/admin/v1/staff/{created['id']}/status",
        json={"status": "disabled", "reason": "临时停用"},
        headers=headers,
    )
    r = client.post(
        f"/admin/v1/staff/{created['id']}/status", json={"status": "active"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["disable_reason"] is None
    _login(client, "ops-lily", "lily-pass-1234")


def test_cannot_disable_self(make, session, client):
    headers, actor = _admin_headers(make, client)
    r = client.post(
        f"/admin/v1/staff/{actor.id}/status", json={"status": "disabled"}, headers=headers
    )
    assert r.status_code == 400


def test_invalid_status_rejected(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    r = client.post(
        f"/admin/v1/staff/{created['id']}/status", json={"status": "deleted"}, headers=headers
    )
    assert r.status_code == 422


def test_missing_staff_returns_404(make, session, client):
    headers, _ = _admin_headers(make, client)
    assert client.patch("/admin/v1/staff/9999", json={"role": "ops"}, headers=headers).status_code == 404
    assert (
        client.post("/admin/v1/staff/9999/status", json={"status": "disabled"}, headers=headers).status_code
        == 404
    )


# ---- passwords ----


def test_reset_password_revokes_sessions_and_swaps_credentials(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    victim = _login(client, "ops-lily", "lily-pass-1234")

    r = client.post(
        f"/admin/v1/staff/{created['id']}/password",
        json={"new_password": "brand-new-pass-9"},
        headers=headers,
    )
    assert r.status_code == 204

    assert client.get("/admin/v1/dramas", headers=victim).status_code == 401
    assert (
        client.post(
            "/admin/v1/auth/login", json={"username": "ops-lily", "password": "lily-pass-1234"}
        ).status_code
        == 401
    )
    _login(client, "ops-lily", "brand-new-pass-9")


def test_self_change_requires_old_password(make, session, client):
    headers, _ = _admin_headers(make, client)
    r = client.post(
        "/admin/v1/auth/password",
        json={"old_password": "wrong-pass-1", "new_password": "another-pass-9"},
        headers=headers,
    )
    assert r.status_code == 400
    # Failed attempt must not log the caller out.
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 200


def test_self_change_revokes_every_session(make, session, client):
    account = make.admin(role="admin")
    headers = _login(client, account.username)

    r = client.post(
        "/admin/v1/auth/password",
        json={"old_password": "pass-1234", "new_password": "my-new-pass-9"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/admin/v1/auth/login",
            json={"username": account.username, "password": "pass-1234"},
        ).status_code
        == 401
    )
    fresh = client.post(
        "/admin/v1/auth/login",
        json={"username": account.username, "password": "my-new-pass-9"},
    )
    assert fresh.status_code == 200


# ---- audit ----


def test_audit_log_records_actions_without_secrets(make, session, client):
    headers, _ = _admin_headers(make, client)
    created = _create(client, headers)
    client.post(
        f"/admin/v1/staff/{created['id']}/status",
        json={"status": "disabled", "reason": "离职"},
        headers=headers,
    )
    client.post(
        f"/admin/v1/staff/{created['id']}/status", json={"status": "active"}, headers=headers
    )
    client.post(
        f"/admin/v1/staff/{created['id']}/password",
        json={"new_password": "secret-pass-2026"},
        headers=headers,
    )

    session.expire_all()
    rows = session.query(AuditLog).filter(AuditLog.target_type == "admin_user").all()
    actions = sorted({row.action for row in rows})
    assert actions == ["staff_create", "staff_disable", "staff_enable", "staff_password_reset"]

    raw = json.dumps([row.payload for row in rows], ensure_ascii=False)
    assert "secret-pass-2026" not in raw
    assert "lily-pass-1234" not in raw
    assert "password" not in raw
