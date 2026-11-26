"""Durable KoSell extensions and review bonuses."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None


def upgrade():
    from app.models import Base

    Base.metadata.tables["kosell_operations"].create(op.get_bind())


def downgrade():
    op.drop_table("kosell_operations")
