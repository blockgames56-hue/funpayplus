"""Durable delivery reservations and unknown lot stock."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "delivery_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("auto_delivery_rules.id", ondelete="SET NULL")),
        sa.Column("item", sa.Text()),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_delivery_reservations_state", "delivery_reservations", ["state"])
    with op.batch_alter_table("lots") as batch:
        batch.alter_column("amount", existing_type=sa.Integer(), nullable=True)


def downgrade():
    # Unknown stock cannot be converted to a number without inventing inventory.
    count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM lots WHERE amount IS NULL"))
    if count:
        raise RuntimeError("Resolve unknown lot stock before downgrading to 0015")
    with op.batch_alter_table("lots") as batch:
        batch.alter_column("amount", existing_type=sa.Integer(), nullable=False)
    op.drop_table("delivery_reservations")
