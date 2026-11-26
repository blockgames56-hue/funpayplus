"""Rules for asynchronous category repricing."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auto_dump_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_url", sa.String(1000), nullable=False),
        sa.Column("absolute_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("step", sa.Numeric(12, 2), nullable=False),
        sa.Column("competitor_price", sa.Numeric(12, 2)),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_error", sa.String(300)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_auto_dump_rules_lot_id", "auto_dump_rules", ["lot_id"], unique=True)


def downgrade():
    op.drop_index("ix_auto_dump_rules_lot_id", table_name="auto_dump_rules")
    op.drop_table("auto_dump_rules")
