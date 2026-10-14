"""Redis 会话存储不可用时的错误语义：明确的 503，而不是裸 500。

背景（2026-10-14 实际故障）：本地 Docker 退出导致 minitv-redis 停止，
require_admin 依赖连接 Redis 抛 ConnectionError，被 FastAPI 兜底成 500，
后台页面只显示「加载失败：API error 500」，看不出真实原因。现在统一转成
带中文说明的 503，前端 errorText() 会直接展示这条说明。
"""

from __future__ import annotations

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from app.main import app
from app.modules.ops import service as ops_service


@pytest.fixture()
def client(session):
    return TestClient(app)


class _DownRedis:
    """Stand-in client whose every command fails like a stopped Redis."""

    def _raise(self):
        raise redis_lib.ConnectionError(
            "Error 61 connecting to 127.0.0.1:16379. Connection refused."
        )

    def get(self, *args, **kwargs):
        self._raise()

    def set(self, *args, **kwargs):
        self._raise()

    def delete(self, *args, **kwargs):
        self._raise()


def _kill_redis(monkeypatch):
    monkeypatch.setattr(ops_service, "_redis_client", _DownRedis())


def test_login_returns_503_when_redis_is_down(monkeypatch, client, make):
    staff = make.admin(role="ops")
    _kill_redis(monkeypatch)

    response = client.post(
        "/admin/v1/auth/login",
        json={"username": staff.username, "password": "pass-1234"},
    )

    assert response.status_code == 503, response.text
    assert "Redis" in response.json()["detail"]


def test_authenticated_request_returns_503_when_redis_is_down(monkeypatch, client, make):
    make.drama(title="Session store drama")
    _kill_redis(monkeypatch)

    response = client.get(
        "/admin/v1/dramas",
        headers={"Authorization": "Bearer any-token"},
    )

    assert response.status_code == 503, response.text
    assert "Redis" in response.json()["detail"]


def test_health_of_session_flows_unaffected_when_redis_is_up(client, make):
    staff = make.admin(role="ops")
    login = client.post(
        "/admin/v1/auth/login",
        json={"username": staff.username, "password": "pass-1234"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]

    me = client.get("/admin/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"] == staff.username

    logout = client.post("/admin/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200, logout.text
    after = client.get("/admin/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 401
