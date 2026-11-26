"""Autolink rental accounts left without a lot binding after data edits."""

from alembic import op
from sqlalchemy import text

revision = "0021"
down_revision = "0020"
branch_labels = depends_on = None


def upgrade():
    """Связать rental_accounts без привязки с первым активным лотом аренды.

    Ранее `/connect` сохранял аккаунт пула без binding_id, если лоты не были
    отмечены, и плагин навсегда оставался в статусе «Требует настройки».
    """
    connection = op.get_bind()
    first_binding = connection.execute(
        text(
            "SELECT id FROM plugin_bindings "
            "WHERE kind = 'steam-rent' AND is_active = 1 AND is_extension = 0 "
            "ORDER BY id LIMIT 1"
        )
    ).scalar()
    if not first_binding:
        return
    orphans = connection.execute(
        text("SELECT id FROM rental_accounts WHERE binding_id IS NULL")
    ).scalars().all()
    for account_id in orphans:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO rental_account_bindings (account_id, binding_id) "
                "VALUES (:account_id, :binding_id)"
            ),
            {"account_id": account_id, "binding_id": first_binding},
        )
        connection.execute(
            text(
                "UPDATE rental_accounts SET binding_id = :binding_id "
                "WHERE id = :account_id"
            ),
            {"account_id": account_id, "binding_id": first_binding},
        )


def downgrade():
    pass
