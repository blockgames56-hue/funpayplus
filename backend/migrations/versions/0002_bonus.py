"""Durable loyalty balances and one award per order."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bonus_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("buyer_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_name", sa.String(100), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("purchases", sa.Integer(), nullable=False),
        sa.UniqueConstraint("account_id", "buyer_id"),
    )
    op.create_index("ix_bonus_balances_account_id", "bonus_balances", ["account_id"])
    op.create_index("ix_bonus_balances_buyer_id", "bonus_balances", ["buyer_id"])
    op.create_table(
        "bonus_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("buyer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), unique=True
        ),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bonus_entries_account_id", "bonus_entries", ["account_id"])
    op.create_index("ix_bonus_entries_buyer_id", "bonus_entries", ["buyer_id"])


def downgrade():
    op.drop_table("bonus_entries")
    op.drop_table("bonus_balances")
