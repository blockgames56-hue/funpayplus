"""Supplier routing and explicit order lot identifiers."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("funpay_lot_id", sa.String(50)))
        batch.create_index("ix_orders_funpay_lot_id", ["funpay_lot_id"])
    op.create_table("app_route_suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("api_type", sa.String(20), nullable=False),
        sa.Column("endpoint_url", sa.String(1000), nullable=False),
        sa.Column("token", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table("app_route_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("funpay_item_id", sa.String(50), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("app_route_suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_item_id", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.UniqueConstraint("account_id", "funpay_item_id"),
    )
    op.create_index("ix_app_route_mappings_account_id", "app_route_mappings", ["account_id"])
    op.create_table("app_route_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mapping_id", sa.Integer(), sa.ForeignKey("app_route_mappings.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result_data", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_route_deliveries_order_id", "app_route_deliveries", ["order_id"], unique=True)


def downgrade():
    op.drop_index("ix_app_route_deliveries_order_id", table_name="app_route_deliveries")
    op.drop_table("app_route_deliveries")
    op.drop_index("ix_app_route_mappings_account_id", table_name="app_route_mappings")
    op.drop_table("app_route_mappings")
    op.drop_table("app_route_suppliers")
    with op.batch_alter_table("orders") as batch:
        batch.drop_index("ix_orders_funpay_lot_id")
        batch.drop_column("funpay_lot_id")
