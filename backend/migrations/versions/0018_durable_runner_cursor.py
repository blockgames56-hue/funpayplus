"""Durable per-chat Runner message cursor and reconciliation timestamp."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    op.add_column("chats", sa.Column("last_message_cursor", sa.BigInteger(), nullable=True))
    op.add_column("chats", sa.Column("runner_baselined", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.add_column("chats", sa.Column("runner_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_chats_runner_checked_at", "chats", ["runner_checked_at"])
    op.execute(
        "UPDATE chats SET last_message_cursor = "
        "(SELECT MAX(funpay_message_id) FROM chat_messages "
        "WHERE chat_messages.chat_id = chats.id), runner_baselined = 1 "
        "WHERE EXISTS (SELECT 1 FROM chat_messages "
        "WHERE chat_messages.chat_id = chats.id AND funpay_message_id IS NOT NULL)"
    )


def downgrade():
    op.drop_index("ix_chats_runner_checked_at", table_name="chats")
    op.drop_column("chats", "runner_checked_at")
    op.drop_column("chats", "runner_baselined")
    op.drop_column("chats", "last_message_cursor")
