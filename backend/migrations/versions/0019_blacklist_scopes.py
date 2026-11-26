"""Allow global, account and chat scopes in the blacklist."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = depends_on = None


def upgrade():
    # SQLite cannot drop an unnamed UNIQUE(user_id) in place. Rebuild the small
    # table, retaining every existing entry as a global exclusion.
    op.create_table(
        "blacklist_scoped",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO blacklist_scoped (id, user_id, username, reason, created_at) "
        "SELECT id, user_id, username, reason, created_at FROM blacklist"
    )
    op.drop_table("blacklist")
    op.rename_table("blacklist_scoped", "blacklist")
    op.create_index("ix_blacklist_user_id", "blacklist", ["user_id"])
    op.create_index("ix_blacklist_account_id", "blacklist", ["account_id"])
    op.create_index("ix_blacklist_chat_id", "blacklist", ["chat_id"])


def downgrade():
    op.create_table(
        "blacklist_global",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # A scoped exclusion cannot be represented by the old schema. Preserve only
    # entries that had global semantics; never broaden a scoped exclusion.
    op.execute(
        "INSERT INTO blacklist_global (id, user_id, username, reason, created_at) "
        "SELECT id, user_id, username, reason, created_at FROM blacklist "
        "WHERE account_id IS NULL AND chat_id IS NULL"
    )
    op.drop_table("blacklist")
    op.rename_table("blacklist_global", "blacklist")
    op.create_index("ix_blacklist_user_id", "blacklist", ["user_id"])
