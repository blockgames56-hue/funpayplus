"""Encrypted operator notes for delivery reconciliation."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = depends_on = None


def upgrade():
    op.add_column("delivery_reservations", sa.Column("resolution_note", sa.Text()))


def downgrade():
    with op.batch_alter_table("delivery_reservations") as batch:
        batch.drop_column("resolution_note")
