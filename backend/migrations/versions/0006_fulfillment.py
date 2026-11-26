"""External digital delivery work queue."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fulfillment_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("target", sa.String(500)),
        sa.Column("guild_id", sa.String(40)),
        sa.Column("boost_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("friend_requested_at", sa.DateTime(timezone=True)),
        sa.Column("alerted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_fulfillment_tasks_order_id", "fulfillment_tasks", ["order_id"], unique=True)


def downgrade():
    op.drop_index("ix_fulfillment_tasks_order_id", table_name="fulfillment_tasks")
    op.drop_table("fulfillment_tasks")
