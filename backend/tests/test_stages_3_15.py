"""Acceptance coverage for stages 3 through 14."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.account import service as accounts
from app.modules.account.models import User, VerificationCode
from app.modules.billing.service import ensure_default_plans, sign_webhook
from app.modules.common.models import AuditLog
from app.modules.content.models import Episode, MediaUpload
from app.modules.content.publish_service import GROUP_LIMIT
from app.modules.content.upload_service import sign_callback
from app.modules.playback.service import image_secret, sign_path, verify_token, video_secret
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from app.modules.wallet.service import InsufficientCoinsError, unlock_episode


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _ops(make, client):
    staff = make.admin(role="ops")
    return _login(client, staff.username)


def _publish_free_episode(make, client, headers):
    drama = make.drama(title="Visible Drama", status="draft", free=1, price=50)
    client.post(f"/admin/v1/dramas/{drama.id}/status", json={"to_status": "in_review"}, headers=headers)
    client.post(f"/admin/v1/dramas/{drama.id}/status", json={"to_status": "published"}, headers=headers)
    episode = make.episode(drama, number=1, status="not_uploaded")
    created = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": "a.mp4", "mime": "video/mp4", "size_bytes": 1000, "width": 720, "height": 1280},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    video_id = created.json()["video_id"]
    body = json.dumps({"video_id": video_id, "status": "finished"}).encode()
    callback = client.post(
        "/v1/bunny/callback",
        content=body,
        headers={"X-Bunny-Signature": sign_callback(body), "Content-Type": "application/json"},
    )
    assert callback.status_code == 200, callback.text
    published = client.post(f"/admin/v1/episodes/{episode.id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return drama, episode


def test_sixty_four_episodes_can_be_filtered(make, client):
    headers = _ops(make, client)
    drama = make.drama(title="Long Drama")
    created = client.post(f"/admin/v1/dramas/{drama.id}/episodes/bulk", json={"count": 64}, headers=headers)
    assert created.status_code == 200
    page = client.get(
        f"/admin/v1/dramas/{drama.id}/episodes",
        params={"q": "64", "page_size": 5},
        headers=headers,
    )
    assert page.status_code == 200
    numbers = [item["episode_number"] for item in page.json()["items"]]
    assert 64 in numbers


def test_upload_rejects_oversize_and_wrong_mime(make, client):
    headers = _ops(make, client)
    drama = make.drama()
    episode = make.episode(drama, number=1)
    too_big = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": "a.mp4", "mime": "video/mp4", "size_bytes": 201 * 1024 * 1024, "width": 720, "height": 1280},
        headers=headers,
    )
    assert too_big.status_code == 422
    bad_mime = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": "a.txt", "mime": "text/plain", "size_bytes": 10, "width": 720, "height": 1280},
        headers=headers,
    )
    assert bad_mime.status_code == 422


def test_upload_accepts_landscape_video(make, client, session):
    """Ops uploads landscape episodes too: orientation must never gate an upload."""
    headers = _ops(make, client)
    drama = make.drama()
    episode = make.episode(drama, number=1)
    created = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": "landscape.mp4", "mime": "video/mp4", "size_bytes": 1000, "width": 1920, "height": 1080},
        headers=headers,
    )
    assert created.status_code == 200, created.text

    session.expire_all()
    row = session.get(Episode, episode.id)
    assert (row.width, row.height) == (1920, 1080)
    upload = session.scalars(
        select(MediaUpload).where(MediaUpload.video_id == created.json()["video_id"])
    ).one()
    assert upload.vertical is False


def test_callback_signature_and_replay(make, client, session):
    headers = _ops(make, client)
    drama = make.drama()
    episode = make.episode(drama, number=1)
    created = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": "a.mp4", "mime": "video/mp4", "size_bytes": 1000, "width": 720, "height": 1280},
        headers=headers,
    )
    video_id = created.json()["video_id"]
    body = json.dumps({"video_id": video_id, "status": "finished"}).encode()
    bad = client.post("/v1/bunny/callback", content=body, headers={"X-Bunny-Signature": "nope", "Content-Type": "application/json"})
    assert bad.status_code == 401
    session.expire_all()
    assert session.get(type(episode), episode.id).status == "transcoding"
    assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "bunny_callback_rejected")) == 1
    ok = client.post("/v1/bunny/callback", content=body, headers={"X-Bunny-Signature": sign_callback(body), "Content-Type": "application/json"})
    assert ok.json()["status"] == "in_review"
    again = client.post("/v1/bunny/callback", content=body, headers={"X-Bunny-Signature": sign_callback(body), "Content-Type": "application/json"})
    assert again.json()["replayed"] is True
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(MediaUpload).where(MediaUpload.video_id == video_id)) == 1


def test_guest_free_playback_and_locked_episode_hides_url(make, client):
    headers = _ops(make, client)
    drama, episode = _publish_free_episode(make, client, headers)
    locked = make.episode(drama, number=2, status="not_uploaded")
    created = client.post(
        f"/admin/v1/episodes/{locked.id}/upload-session",
        json={"filename": "b.mp4", "mime": "video/mp4", "size_bytes": 1000, "width": 720, "height": 1280},
        headers=headers,
    )
    body = json.dumps({"video_id": created.json()["video_id"], "status": "finished"}).encode()
    client.post("/v1/bunny/callback", content=body, headers={"X-Bunny-Signature": sign_callback(body), "Content-Type": "application/json"})
    client.post(f"/admin/v1/episodes/{locked.id}/publish", headers=headers)

    free = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": episode.id})
    assert free.status_code == 200, free.text
    payload = free.json()
    assert payload["allowed"] is True
    assert "token=" in payload["url"]
    assert payload["expires_at"] - int(datetime.now(timezone.utc).timestamp()) <= 300
    # Rendition hint follows the uploaded source (720x1280 -> ladder up to 720P).
    assert payload["renditions"] == ["240P", "360P", "480P", "720P"]

    denied = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": locked.id})
    text = denied.text.lower()
    assert denied.json()["allowed"] is False
    assert "url" not in denied.json()
    assert "token" not in text
    assert "m3u8" not in text


def test_image_token_does_not_verify_as_video():
    signed = sign_path("/images/covers/1/", image_secret(), gen=0)
    assert verify_token(signed["path"], signed["expires_at"], signed["token"], 0, video_secret()) is False


def test_unlock_is_idempotent_and_refuses_negative_balance(make, session, client, monkeypatch):
    headers = _ops(make, client)
    drama, episode = _publish_free_episode(make, client, headers)
    session.expire_all()  # the admin publish ran in another session
    paid = make.episode(drama, number=3, status="published")
    paid.bunny_video_id = "paid-video"
    session.commit()
    user = make.user(email="payer@example.com", coins=50)
    monkeypatch.setattr(accounts, "send_verification_code", lambda **_: "test")
    accounts.issue_code(session, email=user.email, purpose="login")
    # direct wallet path: price is 50, balance is 50
    first = unlock_episode(session, user_id=user.id, episode_id=paid.id, idempotency_key="k1")
    assert first.status == "unlocked"
    for _ in range(4):
        replay = unlock_episode(session, user_id=user.id, episode_id=paid.id, idempotency_key="k1")
        assert replay.replayed is True
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id, CoinLedgerEntry.biz_type == "unlock")) == 1
    broke = make.user(email="broke@example.com", coins=10)
    with pytest.raises(InsufficientCoinsError):
        unlock_episode(session, user_id=broke.id, episode_id=paid.id, idempotency_key="broke-1")
    session.expire_all()
    assert session.get(type(broke), broke.id) is not None


def test_concurrent_unlock_charges_once(make, session, engine):
    drama = make.drama(status="published", price=40, free=0)
    episode = make.episode(drama, number=1, status="published")
    user = make.user(email="race@example.com", coins=40)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    errors = []
    oks = []

    def attempt(key):
        local = factory()
        try:
            result = unlock_episode(local, user_id=user.id, episode_id=episode.id, idempotency_key=key)
            oks.append(result.status)
        except InsufficientCoinsError:
            errors.append("short")
        finally:
            local.close()

    threads = [threading.Thread(target=attempt, args=(f"race-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert oks.count("unlocked") == 1
    session.expire_all()
    from app.modules.wallet.models import WalletAccount
    assert session.get(WalletAccount, user.id).balance == 0
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id)) == 1


def test_member_does_not_spend_coins_and_cancel_keeps_ledger(make, session, client):
    headers = _ops(make, client)
    ensure_default_plans(session)
    drama, _free = _publish_free_episode(make, client, headers)
    session.expire_all()  # the admin publish ran in another session
    paid = make.episode(drama, number=4, status="published")
    user = make.user(email="member@example.com", coins=80)
    make.subscription(user, active=True)
    before = session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id))
    result = unlock_episode(session, user_id=user.id, episode_id=paid.id, idempotency_key="member-1")
    assert result.status == "subscription"
    session.expire_all()
    after = session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id))
    assert after == before
    from app.modules.billing.service import cancel_subscription
    cancel_subscription(session, user.id)
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id)) == after


def test_groups_sort_independently_and_cap_at_limit(make, client):
    headers = _ops(make, client)
    dramas = [make.drama(title=f"G{i}") for i in range(GROUP_LIMIT + 1)]
    first = client.post("/admin/v1/groups", json={"name": "Must Watch", "position": 1}, headers=headers).json()["id"]
    second = client.post("/admin/v1/groups", json={"name": "Popular", "position": 2}, headers=headers).json()["id"]
    for drama in dramas[:GROUP_LIMIT]:
        added = client.post(f"/admin/v1/groups/{first}/dramas", json={"drama_id": drama.id, "confirm": True}, headers=headers)
        assert added.status_code == 200, added.text
    overflow = client.post(f"/admin/v1/groups/{first}/dramas", json={"drama_id": dramas[GROUP_LIMIT].id, "confirm": True}, headers=headers)
    assert overflow.status_code == 409
    other = client.post(f"/admin/v1/groups/{second}/dramas", json={"drama_id": dramas[0].id, "confirm": True}, headers=headers)
    assert other.status_code == 200
    moved = client.patch(f"/admin/v1/groups/{first}/dramas/{dramas[0].id}", json={"position": 7}, headers=headers)
    assert moved.json()["position_in_group"] == 7
    client.patch(f"/admin/v1/groups/{second}", json={"position": 9}, headers=headers)
    still = client.get("/admin/v1/groups", headers=headers).json()["items"]
    assert next(item for item in still if item["id"] == second)["position"] == 9
    assert next(item for item in still if item["id"] == first)["drama_count"] == GROUP_LIMIT


def test_group_editor_rename_membership_and_drama_side(make, client):
    headers = _ops(make, client)
    drama = make.drama(title="Editor Drama")
    other = make.drama(title="Other Drama")
    created = client.post("/admin/v1/groups", json={"name": "Weekly", "position": 0}, headers=headers)
    assert created.status_code == 200, created.text
    group_id = created.json()["id"]

    renamed = client.patch(f"/admin/v1/groups/{group_id}", json={"name": "本周推荐", "status": "inactive"}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "本周推荐"
    assert renamed.json()["status"] == "inactive"

    added = client.post(f"/admin/v1/groups/{group_id}/dramas", json={"drama_id": drama.id, "confirm": True}, headers=headers)
    assert added.status_code == 200, added.text
    client.post(f"/admin/v1/groups/{group_id}/dramas", json={"drama_id": other.id, "confirm": True}, headers=headers)

    members = client.get(f"/admin/v1/groups/{group_id}/dramas", headers=headers).json()["items"]
    assert [item["id"] for item in members] == [drama.id, other.id]

    removed = client.delete(f"/admin/v1/groups/{group_id}/dramas/{other.id}", headers=headers)
    assert removed.status_code == 200, removed.text
    left = client.get(f"/admin/v1/groups/{group_id}/dramas", headers=headers).json()["items"]
    assert [item["id"] for item in left] == [drama.id]

    mine = client.get(f"/admin/v1/dramas/{drama.id}/groups", headers=headers).json()["items"]
    row = next(item for item in mine if item["id"] == group_id)
    assert row["joined"] is True
    assert row["status"] == "inactive"
    public = client.get("/v1/home/groups").json()["groups"]
    assert all(item["id"] != group_id for item in public)


def test_unpublish_hides_and_delete_needs_confirm(make, client):
    headers = _ops(make, client)
    admin = make.admin(role="admin")
    admin_headers = _login(client, admin.username)
    drama, episode = _publish_free_episode(make, client, headers)
    visible = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": episode.id})
    assert visible.json()["allowed"] is True
    client.post(f"/admin/v1/dramas/{drama.id}/status", json={"to_status": "unpublished"}, headers=headers)
    hidden = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": episode.id})
    assert hidden.status_code == 404
    preview = client.post(f"/admin/v1/dramas/{drama.id}/delete", json={"confirm": False}, headers=admin_headers)
    assert preview.status_code == 409
    assert preview.json()["detail"]["confirm_required"] is True
    removed = client.post(f"/admin/v1/dramas/{drama.id}/delete", json={"confirm": True}, headers=admin_headers)
    assert removed.status_code == 200, removed.text
    missing = client.get(f"/admin/v1/dramas/{drama.id}", headers=headers)
    assert missing.status_code == 404


def test_copy_requires_english_and_price_versions_stay_pinned(make, client, session):
    headers = _ops(make, client)
    admin = make.admin(role="admin")
    admin_headers = _login(client, admin.username)
    missing = client.post("/admin/v1/copies", json={"key": "nav.home", "en": "", "zh": "首页"}, headers=headers)
    assert missing.status_code == 422
    saved = client.post("/admin/v1/copies", json={"key": "nav.home", "en": "Home", "zh": ""}, headers=headers)
    assert saved.status_code == 200
    zh = client.get("/v1/config", params={"locale": "zh"})
    assert zh.json()["copies"]["nav.home"] == "Home"
    ensure_default_plans(session)
    user = make.user(email="buyer@example.com", coins=0)
    body = json.dumps({"txn_id": "txn-1", "user_id": user.id, "plan_code": "monthly"}).encode()
    paid = client.post("/v1/billing/webhook", content=body, headers={"X-Pay-Signature": sign_webhook(body), "Content-Type": "application/json"})
    assert paid.status_code == 200, paid.text
    version_id = paid.json()["plan_version_id"]
    for _ in range(4):
        replay = client.post("/v1/billing/webhook", content=body, headers={"X-Pay-Signature": sign_webhook(body), "Content-Type": "application/json"})
        assert replay.json()["replayed"] is True
    changed = client.post("/admin/v1/plans/monthly/price", json={"price_cents": 2500, "benefits": {"items": ["new"]}}, headers=admin_headers)
    assert changed.status_code == 200
    public = client.get("/v1/plans").json()["items"]
    monthly = next(item for item in public if item["code"] == "monthly")
    assert monthly["price_cents"] == 2500
    from app.modules.billing.models import Subscription
    session.expire_all()
    sub = session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active"))
    assert sub.plan_version_id == version_id


def test_google_verified_email_merges_with_existing_user(make, session, client, monkeypatch):
    from app.modules.account.google import GoogleIdentity
    from app.modules.account.models import ThirdPartyIdentity

    existing = make.user(email="same@example.com", coins=20)

    def identity(_token: str) -> GoogleIdentity:
        return GoogleIdentity(
            subject="google-subject-1",
            email="same@example.com",
            email_verified=True,
            name="Same Person",
            picture="",
        )

    monkeypatch.setattr(accounts, "verify_id_token", identity)
    first = client.post("/v1/auth/google", json={"id_token": "signed-token"})
    assert first.status_code == 200, first.text
    assert first.json()["user_id"] == existing.id
    assert first.json()["created"] is False
    assert session.scalar(select(func.count()).select_from(User)) == 1
    link = session.scalar(select(ThirdPartyIdentity).where(ThirdPartyIdentity.provider == "google"))
    assert link is not None and link.user_id == existing.id

    again = client.post("/v1/auth/google", json={"id_token": "signed-token"})
    assert again.json()["user_id"] == existing.id
    assert session.scalar(select(func.count()).select_from(ThirdPartyIdentity)) == 1


def test_google_unverified_email_does_not_merge(make, session, client, monkeypatch):
    from app.modules.account.google import GoogleIdentity

    existing = make.user(email="same@example.com")

    def identity(_token: str) -> GoogleIdentity:
        return GoogleIdentity(
            subject="google-subject-2",
            email=None,
            email_verified=False,
            name="Other",
            picture="",
        )

    monkeypatch.setattr(accounts, "verify_id_token", identity)
    created = client.post("/v1/auth/google", json={"id_token": "signed-token"})
    assert created.status_code == 200, created.text
    assert created.json()["created"] is True
    assert created.json()["user_id"] != existing.id


def test_email_code_login_returns_the_registered_user(client, monkeypatch):
    monkeypatch.setattr(accounts, "generate_code", lambda: "654321")
    monkeypatch.setattr(accounts, "send_verification_code", lambda **_: "test-message")
    registered = client.post("/v1/auth/email/code", json={"email": "Same@Example.com", "purpose": "register"})
    assert registered.status_code == 200, registered.text
    created = client.post("/v1/auth/email/register", json={"email": "same@example.com", "code": "654321"})
    assert created.status_code == 200, created.text
    client.post("/v1/auth/email/code", json={"email": "same@example.com", "purpose": "login"})
    signed_in = client.post("/v1/auth/email/login", json={"email": "same@example.com", "code": "654321"})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["user_id"] == created.json()["user_id"]


def test_verification_code_is_not_kept_when_email_fails(client, session, monkeypatch):
    from app.modules.account.mail import MailError

    def fail(**_):
        raise MailError("down")

    monkeypatch.setattr(accounts, "send_verification_code", fail)
    response = client.post("/v1/auth/email/code", json={"email": "down@example.com", "purpose": "register"})
    assert response.status_code == 503
    assert session.scalar(select(VerificationCode).where(VerificationCode.email == "down@example.com")) is None


def test_register_has_no_password_and_rewards_follow_rules(make, session, client, monkeypatch):
    monkeypatch.setattr(accounts, "generate_code", lambda: "123456")
    monkeypatch.setattr(accounts, "send_verification_code", lambda **_: "test-message")
    client.post("/v1/auth/email/code", json={"email": "new@example.com", "purpose": "register"})
    rejected = client.post("/v1/auth/email/register", json={"email": "new@example.com", "code": "123456", "password": "secret"})
    assert rejected.status_code == 422
    stored = session.scalar(select(VerificationCode).where(VerificationCode.email == "new@example.com"))
    assert stored.code_hash != "123456"
    assert "123456" not in stored.code_hash
    created = client.post("/v1/auth/email/register", json={"email": "new@example.com", "code": "123456"})
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["coins"] == 80
    first = client.post("/v1/me/checkin", headers={"Authorization": f"Bearer {token}"})
    assert first.json()["amount"] == 30
    second = client.post("/v1/me/checkin", headers={"Authorization": f"Bearer {token}"})
    assert second.json()["replayed"] is True
    ad = client.post("/v1/me/ad-reward", headers={"Authorization": f"Bearer {token}"})
    assert ad.status_code == 200
    again = client.post("/v1/me/ad-reward", headers={"Authorization": f"Bearer {token}"})
    assert again.status_code == 409
    oauth = client.post("/v1/auth/oauth/facebook")
    assert oauth.status_code == 501


def test_pending_comments_stay_hidden_and_dashboard_uses_los_angeles(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Commented", status="published")
    episode = make.episode(drama, number=1, status="published")
    user = make.user(email="commenter@example.com", coins=0)
    user.created_at = datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc)
    session.commit()
    from app.modules.social.models import Comment
    session.add(Comment(user_id=user.id, drama_id=drama.id, episode_id=episode.id, body="secret", status="pending"))
    session.commit()
    listed = client.get(f"/v1/dramas/{drama.id}/comments")
    assert listed.json()["items"] == []
    comment_id = session.scalar(select(Comment.id))
    approved = client.post(f"/admin/v1/comments/{comment_id}", json={"status": "approved"}, headers=headers)
    assert approved.status_code == 200
    assert len(client.get(f"/v1/dramas/{drama.id}/comments").json()["items"]) == 1
    board = client.get("/admin/v1/dashboard", params={"day": "2026-10-13"}, headers=headers)
    assert board.status_code == 200, board.text
    assert board.json()["timezone"] == "America/Los_Angeles"
    assert board.json()["new_users"] == 1
    other = client.get("/admin/v1/dashboard", params={"day": "2026-10-14"}, headers=headers)
    assert other.json()["new_users"] == 0


def test_admin_user_list_exposes_profile_fields_and_edit_is_audited(make, session, client):
    admin = make.admin(role="admin")
    headers = _login(client, admin.username)
    user = make.user(email="edit-me@example.com", coins=120)

    listed = client.get("/admin/v1/users", params={"q": "edit-me@example.com"}, headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["items"] if item["id"] == user.id)
    assert row["coins"] == 120
    assert {"email", "avatar_url", "gender", "bio", "region", "locale", "member", "member_until"} <= set(row)

    updated = client.patch(
        f"/admin/v1/users/{user.id}",
        json={"display_name": "Renamed User", "gender": "other", "bio": "hello", "region": "US"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] is True
    session.expire_all()  # the request ran through its own session
    assert session.get(User, user.id).display_name == "Renamed User"
    actions = session.scalars(select(AuditLog.action).where(AuditLog.target_id == user.id)).all()
    assert "user_update" in actions

    make.user(email="taken@example.com")
    taken = client.patch(f"/admin/v1/users/{user.id}", json={"email": "taken@example.com"}, headers=headers)
    assert taken.status_code == 409
    assert client.patch(f"/admin/v1/users/{user.id}", json={"gender": "martian"}, headers=headers).status_code == 422
    assert client.patch(f"/admin/v1/users/{user.id}", json={"display_name": "   "}, headers=headers).status_code == 422
    assert client.patch("/admin/v1/users/999999", json={"display_name": "ghost"}, headers=headers).status_code == 404

    finance = make.admin(role="finance")
    finance_headers = _login(client, finance.username)
    denied = client.patch(f"/admin/v1/users/{user.id}", json={"display_name": "Nope"}, headers=finance_headers)
    assert denied.status_code == 403


def test_adjust_bootstraps_missing_wallet_and_blocks_negative(make, session, client):
    admin = make.admin(role="admin")
    headers = _login(client, admin.username)
    user = make.user(email="no-wallet@example.com", coins=0)
    session.execute(delete(WalletAccount).where(WalletAccount.user_id == user.id))
    session.commit()

    granted = client.post(
        f"/admin/v1/users/{user.id}/coins", json={"delta": 30, "reason": "bootstrap"}, headers=headers
    )
    assert granted.status_code == 200, granted.text
    assert granted.json() == {"before": 0, "delta": 30, "after": 30}
    assert session.get(WalletAccount, user.id).balance == 30

    overdraw = client.post(
        f"/admin/v1/users/{user.id}/coins", json={"delta": -100, "reason": "too much"}, headers=headers
    )
    assert overdraw.status_code == 409
    assert session.get(WalletAccount, user.id).balance == 30

    missing = client.post("/admin/v1/users/999999/coins", json={"delta": 1, "reason": "ghost"}, headers=headers)
    assert missing.status_code == 404

    zero = client.post(f"/admin/v1/users/{user.id}/coins", json={"delta": 0, "reason": "noop"}, headers=headers)
    assert zero.status_code == 422


def test_finance_cannot_adjust_and_playback_ignores_analytics(make, client):
    finance = make.admin(role="finance")
    headers = _login(client, finance.username)
    user = make.user(email="adj@example.com", coins=10)
    denied = client.post(f"/admin/v1/users/{user.id}/coins", json={"delta": 5, "reason": "fix"}, headers=headers)
    assert denied.status_code == 403
    admin = make.admin(role="admin")
    admin_headers = _login(client, admin.username)
    adjusted = client.post(f"/admin/v1/users/{user.id}/coins", json={"delta": 5, "reason": "goodwill"}, headers=admin_headers)
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json() == {"before": 10, "delta": 5, "after": 15}
    ops = _ops(make, client)
    drama, episode = _publish_free_episode(make, client, ops)
    played = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": episode.id})
    assert played.status_code == 200
