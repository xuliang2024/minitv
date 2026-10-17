"""Shared request authentication for client-facing routes.

Both the public flow router and the feed router need the same "resolve the
bearer token if there is one, otherwise treat the caller as a guest" logic, so
it lives here instead of being copied.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account import service as accounts
from app.modules.account.models import User


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Resolve the bearer token when present; guests resolve to ``None``."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    return accounts.resolve_user(db, token)


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    return user
