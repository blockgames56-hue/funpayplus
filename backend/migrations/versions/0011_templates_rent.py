"""Message catalogue overrides, manual leases, shared inventory and buyer history."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    from app.models import Base

    for name in (
        "message_templates",
        "rental_account_bindings",
        "rental_buyers",
        "rental_bonuses",
        "rental_actions",
    ):
        Base.metadata.tables[name].create(op.get_bind())
    with op.batch_alter_table("rental_accounts") as batch:
        batch.alter_column("binding_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("game", sa.String(150), nullable=False, server_default=""))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("rental_leases") as batch:
        batch.alter_column("job_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("chat_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("buyer_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("buyer_name", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("funpay_account_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("request_key", sa.String(100), nullable=True))
        batch.add_column(sa.Column("warned_30_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("end_reason", sa.String(30), nullable=False, server_default="expired")
        )
        batch.create_foreign_key("fk_rent_chat", "chats", ["chat_id"], ["id"], ondelete="RESTRICT")
        batch.create_foreign_key(
            "fk_rent_account", "accounts", ["funpay_account_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_unique_constraint("uq_rent_request", ["request_key"])
        batch.create_index("ix_rental_leases_buyer_id", ["buyer_id"])
    op.execute(
        "INSERT INTO rental_account_bindings (account_id,binding_id) SELECT id,binding_id FROM rental_accounts WHERE binding_id IS NOT NULL"
    )
    op.execute(
        """UPDATE rental_leases SET buyer_id=(SELECT o.buyer_id FROM plugin_jobs j JOIN orders o ON o.id=j.order_id WHERE j.id=rental_leases.job_id), buyer_name=COALESCE((SELECT o.buyer_name FROM plugin_jobs j JOIN orders o ON o.id=j.order_id WHERE j.id=rental_leases.job_id),''), funpay_account_id=(SELECT o.account_id FROM plugin_jobs j JOIN orders o ON o.id=j.order_id WHERE j.id=rental_leases.job_id)"""
    )
    op.execute(
        """UPDATE rental_leases SET chat_id=(SELECT c.id FROM chats c WHERE c.account_id=rental_leases.funpay_account_id AND c.interlocutor_id=rental_leases.buyer_id LIMIT 1)"""
    )


def downgrade():
    raise RuntimeError(
        "Migration contains rental audit history; restore a verified backup to downgrade"
    )
