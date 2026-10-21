"""`app.seed_admin` runs on every deploy, so it must never resurrect accounts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import seed_admin
from app.main import app
from app.modules.ops.models import AdminUser


def _bind(monkeypatch, engine) -> None:
    monkeypatch.setattr(seed_admin, "get_engine", lambda: engine)


def test_seed_only_bootstraps_an_empty_table(engine, session, monkeypatch, capsys):
    _bind(monkeypatch, engine)
    monkeypatch.setenv("SEED_ADMIN_USERNAME", "root")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "seed-pass-1234")

    seed_admin.seed()
    out = capsys.readouterr().out
    assert "created: root" in out
    assert "seed-pass-1234" not in out  # env-provided secrets are not echoed
    assert session.query(AdminUser).count() == 1

    seed_admin.seed()
    assert "skip" in capsys.readouterr().out
    assert session.query(AdminUser).count() == 1

    r = TestClient(app).post(
        "/admin/v1/auth/login", json={"username": "root", "password": "seed-pass-1234"}
    )
    assert r.status_code == 200


def test_seed_generates_a_password_when_none_is_configured(engine, session, monkeypatch, capsys):
    _bind(monkeypatch, engine)
    monkeypatch.delenv("SEED_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)

    seed_admin.seed()
    out = capsys.readouterr().out
    assert "created: admin" in out
    marker = "initial password (shown once):"
    assert marker in out
    password = out.split(marker, 1)[1].strip()

    r = TestClient(app).post(
        "/admin/v1/auth/login", json={"username": "admin", "password": password}
    )
    assert r.status_code == 200
