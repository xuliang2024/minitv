"""Admin settlement console: generate, inspect, confirm and mark paid."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.ops import settlements as service
from app.modules.ops.models import ROLE_ADMIN, ROLE_FINANCE, AdminUser, Supplier, SupplierSettlement
from app.modules.ops.router import require_admin

router = APIRouter(prefix="/admin/v1", tags=["settlements"])
MONEY_ROLES = (ROLE_FINANCE, ROLE_ADMIN)


def _require_money(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role not in MONEY_ROLES:
        raise HTTPException(status_code=403, detail="role cannot access settlements")
    return user


class GenerateIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")


class StatusIn(BaseModel):
    status: str = Field(pattern="^(confirmed|paid)$")
    note: str = Field(default="", max_length=500)


def _out(row: SupplierSettlement, supplier: Supplier | None) -> dict:
    return {
        "id": row.id,
        "supplier": {"id": supplier.id, "name": supplier.name} if supplier else None,
        "month": row.period_start.strftime("%Y-%m"),
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "currency": row.currency,
        "gross_cents": row.gross_cents,
        "revenue_cents": row.revenue_cents,
        "effective_share_bps": row.effective_share_bps,
        "order_count": row.order_count,
        "status": row.status,
        "note": row.note,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
    }


@router.get("/settlements")
def list_settlements(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(_require_money),
):
    stmt = (
        select(SupplierSettlement, Supplier)
        .outerjoin(Supplier, Supplier.id == SupplierSettlement.supplier_id)
    )
    if month:
        try:
            start, _ = service.month_bounds(month)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stmt = stmt.where(SupplierSettlement.period_start == start)
    if status:
        stmt = stmt.where(SupplierSettlement.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(SupplierSettlement.period_start.desc(), SupplierSettlement.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_out(row, supplier) for row, supplier in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/settlements/generate")
def generate(
    body: GenerateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(_require_money),
):
    try:
        result = service.generate(db, body.month, actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="settlements_generate",
            target_type="settlement",
            target_id=0,
            payload=result,
        )
    )
    db.commit()
    return result


@router.post("/settlements/{settlement_id}/status")
def change_status(
    settlement_id: int,
    body: StatusIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(_require_money),
):
    row = db.get(SupplierSettlement, settlement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="settlement not found")
    if body.status == "confirmed":
        if row.status != "draft":
            raise HTTPException(status_code=409, detail="只有草稿可以确认")
        row.status = "confirmed"
        row.confirmed_at = datetime.now(timezone.utc)
    else:
        if row.status != "confirmed":
            raise HTTPException(status_code=409, detail="请先确认对账单")
        row.status = "paid"
        row.paid_at = datetime.now(timezone.utc)
    if body.note.strip():
        row.note = body.note.strip()
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action=f"settlement_{body.status}",
            target_type="settlement",
            target_id=row.id,
            payload={"month": row.period_start.isoformat(), "status": row.status},
        )
    )
    db.commit()
    return _out(row, db.get(Supplier, row.supplier_id))
