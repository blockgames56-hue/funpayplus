"""Durable plugin orders, account rentals, and support requests."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auto_dump_rules",
        sa.Column(
            "options", sa.Text(), nullable=False, server_default='{"convert_commission":true}'
        ),
    )
    op.create_table(
        "plugin_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "lot_id",
            sa.Integer(),
            sa.ForeignKey("lots.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("service_id", sa.String(100), nullable=False),
        sa.Column("is_extension", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_plugin_bindings_kind", "plugin_bindings", ["kind"])
    op.create_table(
        "plugin_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "binding_id", sa.Integer(), sa.ForeignKey("plugin_bindings.id", ondelete="SET NULL")
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(1000)),
        sa.Column("remote_id", sa.String(200)),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("order_id", "kind", "state", "next_check_at"):
        op.create_index(
            f"ix_plugin_jobs_{column}", "plugin_jobs", [column], unique=column == "order_id"
        )
    op.create_table(
        "rental_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "binding_id",
            sa.Integer(),
            sa.ForeignKey("plugin_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("pending_password", sa.Text()),
        sa.Column("last_error", sa.String(500)),
    )
    for column in ("binding_id", "state"):
        op.create_index(f"ix_rental_accounts_{column}", "rental_accounts", [column])
    op.create_table(
        "rental_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("plugin_jobs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("rental_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warned_at", sa.DateTime(timezone=True)),
        sa.Column("bonus_awarded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("account_id", "state", "expires_at"):
        op.create_index(f"ix_rental_leases_{column}", "rental_leases", [column])
    op.create_table(
        "support_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("remote_id", sa.String(100)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_requests_account_id", "support_requests", ["account_id"])
    op.create_table(
        "support_request_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("support_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
    )
    op.create_index(
        "ix_support_request_orders_request_id", "support_request_orders", ["request_id"]
    )


def downgrade():
    for table in (
        "support_request_orders",
        "support_requests",
        "rental_leases",
        "rental_accounts",
        "plugin_jobs",
        "plugin_bindings",
    ):
        op.drop_table(table)
    op.drop_column("auto_dump_rules", "options")
