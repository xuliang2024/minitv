"""supplier (partner) tenancy, review state, and membership attribution

P1 of docs/supplier-role-plan.md. Three things land here:

* a `supplier` tenant + one login account per supplier (`admin_users.supplier_id`
  partial-unique, role `partner`);
* drama ownership + `review_state`, kept orthogonal to `dramas.status` so the
  existing DB trigger and client visibility queries are untouched;
* `orders.source_drama_id` / `subscriptions.source_drama_id` so membership
  purchases can be attributed to the drama they came from. This has to ship
  before the partner UI: attribution cannot be backfilled.

Titles stop being globally unique: two suppliers may carry the same name.
Self-operated dramas keep global uniqueness through a partial index.

Revision ID: d3f8b1c6a940
Revises: f1a7c2e93b64
Create Date: 2026-10-22 10:05:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "d3f8b1c6a940"
down_revision = "f1a7c2e93b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("contact_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("revenue_share_bps", sa.Integer(), nullable=False, server_default="8000"),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("revenue_share_bps BETWEEN 0 AND 10000", name="ck_suppliers_share"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_suppliers_status"),
    )

    op.create_table(
        "supplier_share_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=False
        ),
        sa.Column("share_bps", sa.Integer(), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "changed_by", sa.BigInteger(), sa.ForeignKey("admin_users.id"), nullable=True
        ),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.CheckConstraint("share_bps BETWEEN 0 AND 10000", name="ck_share_history_bps"),
    )
    op.create_index(
        "ix_supplier_share_lookup", "supplier_share_history", ["supplier_id", "effective_from"]
    )

    op.add_column("admin_users", sa.Column("supplier_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_admin_users_supplier", "admin_users", "suppliers", ["supplier_id"], ["id"]
    )
    op.drop_constraint("ck_admin_users_role", "admin_users", type_="check")
    op.create_check_constraint(
        "ck_admin_users_role", "admin_users", "role IN ('ops', 'finance', 'admin', 'partner')"
    )
    op.create_check_constraint(
        "ck_admin_users_partner_supplier",
        "admin_users",
        "role <> 'partner' OR supplier_id IS NOT NULL",
    )
    op.create_index(
        "uq_admin_users_supplier",
        "admin_users",
        ["supplier_id"],
        unique=True,
        postgresql_where=sa.text("supplier_id IS NOT NULL"),
    )

    op.add_column("dramas", sa.Column("supplier_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_dramas_supplier", "dramas", "suppliers", ["supplier_id"], ["id"])
    op.add_column(
        "dramas",
        sa.Column("review_state", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.add_column("dramas", sa.Column("review_note", sa.String(length=500), nullable=True))
    op.add_column("dramas", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dramas", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dramas", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_dramas_reviewed_by", "dramas", "admin_users", ["reviewed_by"], ["id"]
    )
    op.create_check_constraint(
        "ck_dramas_review_state",
        "dramas",
        "review_state IN ('none', 'pending', 'approved', 'rejected')",
    )
    op.create_index("ix_dramas_supplier_status", "dramas", ["supplier_id", "status"])
    op.create_index(
        "ix_dramas_review_state",
        "dramas",
        ["review_state"],
        postgresql_where=sa.text("review_state = 'pending'"),
    )

    op.drop_constraint("uq_dramas_title", "dramas", type_="unique")
    op.create_index(
        "uq_dramas_title_self",
        "dramas",
        ["title"],
        unique=True,
        postgresql_where=sa.text("supplier_id IS NULL"),
    )
    op.create_index(
        "uq_dramas_title_supplier",
        "dramas",
        ["supplier_id", "title"],
        unique=True,
        postgresql_where=sa.text("supplier_id IS NOT NULL"),
    )

    op.add_column("orders", sa.Column("source_drama_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_orders_source_drama", "orders", "dramas", ["source_drama_id"], ["id"]
    )
    op.create_index(
        "ix_orders_source_drama",
        "orders",
        ["source_drama_id", "paid_at"],
        postgresql_where=sa.text("source_drama_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_refunded_at",
        "orders",
        ["refunded_at"],
        postgresql_where=sa.text("refunded_at IS NOT NULL"),
    )

    op.add_column("subscriptions", sa.Column("source_drama_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_subscriptions_source_drama", "subscriptions", "dramas", ["source_drama_id"], ["id"]
    )

    op.create_index(
        "ix_playback_events_episode_created",
        "playback_events",
        ["episode_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_playback_events_episode_created", table_name="playback_events")

    op.drop_constraint(
        "fk_subscriptions_source_drama", "subscriptions", type_="foreignkey"
    )
    op.drop_column("subscriptions", "source_drama_id")

    op.drop_index("ix_orders_refunded_at", table_name="orders")
    op.drop_index("ix_orders_source_drama", table_name="orders")
    op.drop_constraint("fk_orders_source_drama", "orders", type_="foreignkey")
    op.drop_column("orders", "refunded_at")
    op.drop_column("orders", "source_drama_id")

    # NOTE: this fails if two suppliers already own the same title — downgrade
    # is only safe before partner content exists.
    op.drop_index("uq_dramas_title_supplier", table_name="dramas")
    op.drop_index("uq_dramas_title_self", table_name="dramas")
    op.create_unique_constraint("uq_dramas_title", "dramas", ["title"])

    op.drop_index("ix_dramas_review_state", table_name="dramas")
    op.drop_index("ix_dramas_supplier_status", table_name="dramas")
    op.drop_constraint("ck_dramas_review_state", "dramas", type_="check")
    op.drop_constraint("fk_dramas_reviewed_by", "dramas", type_="foreignkey")
    op.drop_column("dramas", "reviewed_by")
    op.drop_column("dramas", "reviewed_at")
    op.drop_column("dramas", "submitted_at")
    op.drop_column("dramas", "review_note")
    op.drop_column("dramas", "review_state")
    op.drop_constraint("fk_dramas_supplier", "dramas", type_="foreignkey")
    op.drop_column("dramas", "supplier_id")

    op.drop_index("uq_admin_users_supplier", table_name="admin_users")
    op.drop_constraint("ck_admin_users_partner_supplier", "admin_users", type_="check")
    op.drop_constraint("ck_admin_users_role", "admin_users", type_="check")
    op.create_check_constraint(
        "ck_admin_users_role", "admin_users", "role IN ('ops', 'finance', 'admin')"
    )
    op.drop_constraint("fk_admin_users_supplier", "admin_users", type_="foreignkey")
    op.drop_column("admin_users", "supplier_id")

    op.drop_index("ix_supplier_share_lookup", table_name="supplier_share_history")
    op.drop_table("supplier_share_history")
    op.drop_table("suppliers")
