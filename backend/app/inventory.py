"""Cross-pool checks under the single-process delivery lock; never log keys."""

from sqlalchemy import select

from .funpay import FunPayError
from .models import AutoDeliveryRule, DeliveryReservation
from .security import vault


class InventoryConflict(FunPayError):
    pass


async def ensure_keys_available(db, keys, *, exclude_rule=None, exclude_reservation=None):
    wanted = set(keys)
    if len(wanted) != len(keys):
        raise InventoryConflict("В списке есть повторяющиеся ключи")
    for rule in (await db.scalars(select(AutoDeliveryRule))).all():
        if rule.id == exclude_rule or not rule.keys_pool:
            continue
        if wanted.intersection(vault.decrypt(rule.keys_pool).splitlines()):
            raise InventoryConflict("Ключ уже находится в другом пуле")
    rows = await db.scalars(select(DeliveryReservation).where(
        DeliveryReservation.item.is_not(None), DeliveryReservation.state != "released"))
    for row in rows:
        if row.id != exclude_reservation and vault.decrypt(row.item) in wanted:
            raise InventoryConflict("Ключ уже зарезервирован или выдан")
