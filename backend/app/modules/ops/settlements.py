"""Monthly supplier settlements.

Rules (see docs/supplier-role-plan.md §6):

* only the **first paid order of each user ever** is attributed, and only when
  that order carries a ``source_drama_id`` belonging to the supplier;
* the share is the one effective at the moment that order was paid, so a
  mid-month change splits correctly;
* refunded orders drop out on the next regeneration;
* confirmed/paid statements are never overwritten.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.billing.models import Order
from app.modules.content.models import Drama
from app.modules.ops.models import Supplier, SupplierSettlement, SupplierShareHistory


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-11-01) with the end exclusive."""
    try:
        year, mon = (int(part) for part in month.split("-", 1))
        start = date(year, mon, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError("month must be YYYY-MM") from exc
    if not 1 <= mon <= 12:
        raise ValueError("month must be YYYY-MM")
    last = monthrange(year, mon)[1]
    end = date.fromordinal(date(year, mon, last).toordinal() + 1)
    return start, end


def _share_at(history: list[SupplierShareHistory], at: datetime, fallback: int) -> int:
    chosen = fallback
    for row in sorted(history, key=lambda item: item.effective_from):
        start = row.effective_from
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = row.effective_to
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start <= at and (end is None or at < end):
            chosen = row.share_bps
    return chosen


def first_orders_for_supplier(
    session: Session, supplier_id: int, start: date, end: date
) -> list[Order]:
    """First-ever paid order per user, paid inside the window, from this supplier."""
    drama_ids = select(Drama.id).where(Drama.supplier_id == supplier_id)
    ranked = (
        select(
            Order.id.label("id"),
            func.row_number()
            .over(partition_by=Order.user_id, order_by=(Order.paid_at.asc(), Order.id.asc()))
            .label("rn"),
        )
        .where(Order.status == "paid", Order.refunded_at.is_(None))
        .subquery()
    )
    stmt = (
        select(Order)
        .join(ranked, ranked.c.id == Order.id)
        .where(
            ranked.c.rn == 1,
            Order.paid_at >= datetime.combine(start, time.min, tzinfo=timezone.utc),
            Order.paid_at < datetime.combine(end, time.min, tzinfo=timezone.utc),
            Order.source_drama_id.in_(drama_ids),
        )
    )
    return list(session.scalars(stmt))


def revenue_for_orders(
    session: Session, supplier: Supplier, orders: list[Order]
) -> dict:
    history = list(
        session.scalars(
            select(SupplierShareHistory)
            .where(SupplierShareHistory.supplier_id == supplier.id)
            .order_by(SupplierShareHistory.effective_from)
        )
    )
    gross = 0
    revenue = 0
    bps_weight = 0
    for order in orders:
        paid_at = order.paid_at
        if paid_at.tzinfo is None:
            paid_at = paid_at.replace(tzinfo=timezone.utc)
        bps = _share_at(history, paid_at, supplier.revenue_share_bps)
        gross += order.amount_cents
        revenue += order.amount_cents * bps // 10000
        bps_weight += order.amount_cents * bps
    return {
        "order_count": len(orders),
        "gross_cents": gross,
        "revenue_cents": revenue,
        # amount-weighted average, not revenue/gross: the latter loses 2bps to
        # integer rounding on a single order (1599/1999 -> 7998).
        "effective_share_bps": (bps_weight // gross) if gross else supplier.revenue_share_bps,
        "currency": orders[0].currency if orders else "USD",
    }


def compute(session: Session, supplier: Supplier, start: date, end: date) -> dict:
    orders = first_orders_for_supplier(session, supplier.id, start, end)
    return {"supplier_id": supplier.id, **revenue_for_orders(session, supplier, orders)}


def generate(session: Session, month: str, actor_id: int) -> dict:
    start, end = month_bounds(month)
    created = updated = skipped = 0
    for supplier in session.scalars(select(Supplier).order_by(Supplier.id)):
        data = compute(session, supplier, start, end)
        row = session.scalar(
            select(SupplierSettlement).where(
                SupplierSettlement.supplier_id == supplier.id,
                SupplierSettlement.period_start == start,
            )
        )
        # No orders and no statement yet → nothing to say. An existing draft
        # still gets rewritten, otherwise a refund would leave stale numbers.
        if data["order_count"] == 0 and row is None:
            continue
        if row is not None and row.status != "draft":
            skipped += 1
            continue
        if row is None:
            row = SupplierSettlement(
                supplier_id=supplier.id,
                period_start=start,
                period_end=end,
            )
            session.add(row)
            created += 1
        else:
            updated += 1
        row.currency = data["currency"]
        row.gross_cents = data["gross_cents"]
        row.revenue_cents = data["revenue_cents"]
        row.effective_share_bps = data["effective_share_bps"]
        row.order_count = data["order_count"]
    session.commit()
    return {"month": month, "created": created, "updated": updated, "skipped": skipped}
