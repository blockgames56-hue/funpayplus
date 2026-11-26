"""Track LZT purchases and uncertain outcomes without replaying buys."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("game", sa.String(100)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("credentials", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_market_purchases_item_id", "market_purchases", ["item_id"], unique=True)


def downgrade():
    op.drop_index("ix_market_purchases_item_id", table_name="market_purchases")
    op.drop_table("market_purchases")
