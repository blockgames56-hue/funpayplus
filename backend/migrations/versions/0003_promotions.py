"""Time-limited lot price promotions."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lot_promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lot_id", sa.Integer(), sa.ForeignKey("lots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("original_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discounted_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lot_promotions_account_id", "lot_promotions", ["account_id"])
    op.create_index("ix_lot_promotions_lot_id", "lot_promotions", ["lot_id"])


def downgrade():
    op.drop_table("lot_promotions")
