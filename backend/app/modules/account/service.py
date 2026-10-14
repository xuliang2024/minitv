"""Email codes, registration, profile, check-in and ad rewards.

Verification codes are stored as hashes. The plaintext is never logged or returned by the API.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.account.mail import MailError, send_email
from app.modules.account.models import ThirdPartyIdentity, User, VerificationCode
from app.modules.common.models import AuditLog
from app.modules.ops.config_models import AdClaim, Checkin, RewardConfig
from app.modules.ops.service import get_redis
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount

LA = ZoneInfo("America/Los_Angeles")
USER_SESSION_PREFIX = "minitv_user_session:"
DEFAULT_CHECKIN = [30, 30, 50, 50, 30, 60, 90]
DEFAULT_AMOUNTS = {
    "register": [80],
    "bind_email": [30],
    "bind_facebook": [80],
    "ad": [30],
}
REWARD_CONFIG_ACTION = "reward_config_update"
MAX_REWARD_AMOUNT = 1_000_000

# Single source of truth for the coin-task list: the admin console renders it
# as-is and the client reward screen is expected to reuse the same codes.
REWARD_TASKS: tuple[dict, ...] = (
    {
        "code": "checkin",
        "name": "7 天签到梯度",
        "description": "连续签到第 1-7 天依次发放；中断后从第 1 天重新计算",
        "kind": "gradient",
        "amounts": list(DEFAULT_CHECKIN),
    },
    {
        "code": "register",
        "name": "注册奖励",
        "description": "邮箱注册成功后一次性发放",
        "kind": "single",
        "amounts": list(DEFAULT_AMOUNTS["register"]),
    },
    {
        "code": "bind_email",
        "name": "绑定邮箱",
        "description": "首次绑定邮箱时发放，只发一次",
        "kind": "single",
        "amounts": list(DEFAULT_AMOUNTS["bind_email"]),
    },
    {
        "code": "bind_facebook",
        "name": "绑定 Facebook",
        "description": "首次绑定 Facebook 时发放，只发一次",
        "kind": "single",
        "amounts": list(DEFAULT_AMOUNTS["bind_facebook"]),
    },
    {
        "code": "ad",
        "name": "每日广告奖励",
        "description": "每天可领一次，同日重复请求不二次入账",
        "kind": "single",
        "amounts": list(DEFAULT_AMOUNTS["ad"]),
    },
)
REWARD_TASK_BY_CODE = {task["code"]: task for task in REWARD_TASKS}


def reward_task_catalog() -> list[dict]:
    """A defensive copy of the catalog (callers may mutate the amounts)."""
    return [dict(task, amounts=list(task["amounts"])) for task in REWARD_TASKS]


class AccountError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def la_today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(LA).date()


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def issue_code(session: Session, *, email: str, purpose: str) -> str:
    client = get_redis()
    key = f"code_rate:{purpose}:{email}"
    count = client.incr(key)
    if count == 1:
        client.expire(key, 3600)
    if count > 5:
        raise AccountError(429, "too many codes")
    code = generate_code()
    row = VerificationCode(
        email=email,
        purpose=purpose,
        code_hash=_hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    session.add(row)
    session.commit()
    try:
        send_verification_code(to=email, code=code, purpose=purpose)
    except MailError as exc:
        session.delete(row)
        session.commit()
        raise AccountError(503, "email delivery failed") from exc
    return code


def send_verification_code(*, to: str, code: str, purpose: str) -> str:
    """Send a one-time code. The plaintext is only in this message, never logged."""
    subject = {
        "register": "Your MiniTV verification code",
        "login": "Your MiniTV sign-in code",
        "bind": "Your MiniTV email binding code",
    }.get(purpose, "Your MiniTV verification code")
    text = f"Your MiniTV code is {code}. It expires in 10 minutes. If you did not request it, ignore this email."
    html = (
        f"<p>Your MiniTV code is <strong>{code}</strong>.</p>"
        "<p>It expires in 10 minutes. If you did not request it, ignore this email.</p>"
    )
    return send_email(to=to, subject=subject, text=text, html=html)


def _credit(session: Session, user_id: int, amount: int, biz_type: str, biz_key: str) -> int:
    wallet = session.get(WalletAccount, user_id, with_for_update=True)
    if wallet is None:
        wallet = WalletAccount(user_id=user_id, balance=0)
        session.add(wallet)
        session.flush()
    wallet.balance += amount
    session.add(
        CoinLedgerEntry(
            user_id=user_id,
            amount=amount,
            balance_after=wallet.balance,
            biz_type=biz_type,
            biz_key=biz_key,
        )
    )
    return wallet.balance


def _active_amount(session: Session, code: str, today: date | None = None) -> int:
    today = today or la_today()
    row = session.scalar(
        select(RewardConfig)
        .where(RewardConfig.code == code, RewardConfig.effective_on <= today)
        .order_by(RewardConfig.effective_on.desc())
    )
    amounts = row.amounts if row is not None else DEFAULT_AMOUNTS.get(code, DEFAULT_CHECKIN)
    return int(amounts[0])


def register(session: Session, *, email: str, code: str) -> tuple[User, str]:
    row = session.scalar(
        select(VerificationCode)
        .where(VerificationCode.email == email, VerificationCode.purpose == "register")
        .order_by(VerificationCode.id.desc())
    )
    if row is None or row.expires_at < datetime.now(timezone.utc):
        raise AccountError(400, "code expired")
    if row.attempts >= 5 or not hmac.compare_digest(row.code_hash, _hash_code(code)):
        row.attempts += 1
        session.commit()
        raise AccountError(400, "invalid code")
    if session.scalar(select(User).where(User.email == email)):
        raise AccountError(409, "email already registered")
    user = User(email=email, display_name=email.split("@")[0], public_id=str(secrets.randbelow(9_000_000_000) + 1_000_000_000))
    session.add(user)
    session.flush()
    session.add(WalletAccount(user_id=user.id, balance=0))
    session.flush()
    _credit(session, user.id, _active_amount(session, "register"), "register_reward", f"register:{user.id}")
    token = secrets.token_urlsafe(32)
    get_redis().set(USER_SESSION_PREFIX + token, str(user.id), ex=60 * 60 * 24 * 30)
    session.commit()
    return user, token


def resolve_user(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    user_id = get_redis().get(USER_SESSION_PREFIX + token)
    if user_id is None:
        return None
    return session.get(User, int(user_id))


def oauth_login(session: Session, *, provider: str) -> None:
    flag = os.environ.get(f"OAUTH_{provider.upper()}_ENABLED", "")
    if flag != "1":
        raise AccountError(501, f"{provider} is not configured")


def check_in(session: Session, user: User, *, today: date | None = None) -> dict:
    today = today or la_today()
    existing = session.scalar(select(Checkin).where(Checkin.user_id == user.id, Checkin.day == today))
    if existing is not None:
        return {"streak": existing.streak, "amount": 0, "replayed": True, "balance": session.get(WalletAccount, user.id).balance}
    yesterday = session.scalar(select(Checkin).where(Checkin.user_id == user.id, Checkin.day == today - timedelta(days=1)))
    streak = 1 if yesterday is None else yesterday.streak + 1
    if streak > 7:
        streak = 1
    row = session.scalar(
        select(RewardConfig)
        .where(RewardConfig.code == "checkin", RewardConfig.effective_on <= today)
        .order_by(RewardConfig.effective_on.desc())
    )
    gradient = list(row.amounts) if row is not None else list(DEFAULT_CHECKIN)
    amount = int(gradient[(streak - 1) % 7])
    session.add(Checkin(user_id=user.id, day=today, streak=streak, amount=amount))
    balance = _credit(session, user.id, amount, "checkin", f"checkin:{user.id}:{today.isoformat()}")
    session.commit()
    return {"streak": streak, "amount": amount, "replayed": False, "balance": balance}


def claim_ad(session: Session, user: User, *, today: date | None = None) -> dict:
    today = today or la_today()
    existing = session.scalar(select(AdClaim).where(AdClaim.user_id == user.id, AdClaim.day == today))
    if existing is not None:
        raise AccountError(409, "ad reward already claimed")
    amount = _active_amount(session, "ad", today)
    session.add(AdClaim(user_id=user.id, day=today, amount=amount))
    balance = _credit(session, user.id, amount, "ad_reward", f"ad:{user.id}:{today.isoformat()}")
    session.commit()
    return {"amount": amount, "balance": balance}


def validate_reward_amounts(code: str, amounts: list[int]) -> list[int]:
    """Normalise and validate one task's amounts; raises AccountError(422)."""
    task = REWARD_TASK_BY_CODE.get(code)
    if task is None:
        raise AccountError(422, f"unknown reward code '{code}'")
    cleaned: list[int] = []
    for value in amounts:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AccountError(422, "amounts must be integers")
        if value < 1 or value > MAX_REWARD_AMOUNT:
            raise AccountError(422, f"amounts must be between 1 and {MAX_REWARD_AMOUNT}")
        cleaned.append(value)
    if len(cleaned) != len(task["amounts"]):
        raise AccountError(422, f"{code} expects {len(task['amounts'])} amount(s), got {len(cleaned)}")
    return cleaned


def reward_config_overview(session: Session, *, today: date | None = None) -> dict:
    """Current + pending (next-day) reward config for every task."""
    today = today or la_today()
    items = []
    for task in REWARD_TASKS:
        rows = session.scalars(
            select(RewardConfig)
            .where(RewardConfig.code == task["code"])
            .order_by(RewardConfig.effective_on.desc())
        ).all()
        current = next((row for row in rows if row.effective_on <= today), None)
        pending = next((row for row in rows if row.effective_on > today), None)
        items.append(
            {
                "code": task["code"],
                "name": task["name"],
                "description": task["description"],
                "kind": task["kind"],
                "size": len(task["amounts"]),
                "default_amounts": list(task["amounts"]),
                "current": {
                    "amounts": list(current.amounts) if current is not None else list(task["amounts"]),
                    "effective_on": current.effective_on.isoformat() if current is not None else None,
                    "is_default": current is None,
                },
                "pending": (
                    {"amounts": list(pending.amounts), "effective_on": pending.effective_on.isoformat()}
                    if pending is not None
                    else None
                ),
            }
        )
    return {
        "today": today.isoformat(),
        "next_effective_on": (today + timedelta(days=1)).isoformat(),
        "items": items,
    }


def recent_reward_changes(session: Session, *, limit: int = 10) -> list[dict]:
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.action == REWARD_CONFIG_ACTION)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "code": row.payload.get("code"),
            "amounts": row.payload.get("amounts"),
            "effective_on": row.payload.get("effective_on"),
            "actor": row.payload.get("actor") or (f"#{row.actor_id}" if row.actor_id else "system"),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def save_reward_config(session: Session, *, code: str, amounts: list[int], today: date | None = None) -> RewardConfig:
    """Queue a next-day config change; a same-day re-save updates that pending row."""
    today = today or la_today()
    cleaned = validate_reward_amounts(code, list(amounts))
    effective_on = today + timedelta(days=1)
    row = session.scalar(
        select(RewardConfig).where(
            RewardConfig.code == code, RewardConfig.effective_on == effective_on
        )
    )
    if row is None:
        row = RewardConfig(code=code, amounts=cleaned, effective_on=effective_on)
        session.add(row)
    else:
        row.amounts = cleaned
    session.commit()
    return row


def bind_provider(session: Session, user: User, *, provider: str, provider_user_id: str) -> dict:
    existing = session.scalar(
        select(ThirdPartyIdentity).where(
            ThirdPartyIdentity.user_id == user.id, ThirdPartyIdentity.provider == provider
        )
    )
    if existing is not None:
        return {"bound": True, "rewarded": False}
    session.add(ThirdPartyIdentity(user_id=user.id, provider=provider, provider_user_id=provider_user_id))
    code = "bind_facebook" if provider == "facebook" else "bind_email" if provider == "email" else None
    rewarded = False
    if code:
        _credit(session, user.id, _active_amount(session, code), code, f"{code}:{user.id}")
        rewarded = True
    session.commit()
    return {"bound": True, "rewarded": rewarded}
