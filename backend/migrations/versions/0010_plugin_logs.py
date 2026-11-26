"""Attribute activity to individual plugins without mixing their journals."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("activities", sa.Column("source_plugin", sa.String(30), nullable=True))
    op.create_index("ix_activities_source_plugin", "activities", ["source_plugin"])


def downgrade():
    op.drop_index("ix_activities_source_plugin", table_name="activities")
    op.drop_column("activities", "source_plugin")
