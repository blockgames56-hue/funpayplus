"""Independent KoSell rental connector and durable message queue."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade():
    from app.models import Base

    for name in ("kosell_rentals", "kosell_messages"):
        Base.metadata.tables[name].create(op.get_bind())


def downgrade():
    op.drop_table("kosell_messages")
    op.drop_table("kosell_rentals")
