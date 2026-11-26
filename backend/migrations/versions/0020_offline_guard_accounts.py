"""Guard-code plugin storage: Steam accounts with maFile, credentials encrypted."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "offline_guard_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("offline_guard_accounts")
