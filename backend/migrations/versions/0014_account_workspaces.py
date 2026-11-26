"""Account profile metrics and durable automatic response events."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("rating", sa.Numeric(3, 2), nullable=True))
    op.add_column("accounts", sa.Column("review_count", sa.Integer(), nullable=True))
    from app.models import Base

    Base.metadata.tables["reply_events"].create(op.get_bind())


def downgrade():
    op.drop_table("reply_events")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("rating")
        batch.drop_column("review_count")
