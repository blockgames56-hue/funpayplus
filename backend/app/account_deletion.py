"""Unlink reusable rental inventory before cascading a local FunPay account deletion."""

from sqlalchemy import delete, or_, select, update

from .models import (
    Chat,
    KosellMessage,
    KosellOperation,
    KosellRental,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalLease,
)


async def unlink_account_dependents(db, ident: int) -> None:
    """Run in the caller's transaction; never delete Steam credentials or leases."""
    chats = select(Chat.id).where(Chat.account_id == ident)
    orders = select(Order.id).where(Order.account_id == ident)
    lots = select(Lot.id).where(Lot.account_id == ident)
    bindings = select(PluginBinding.id).where(PluginBinding.lot_id.in_(lots))
    jobs = select(PluginJob.id).where(PluginJob.order_id.in_(orders))

    # These nullable references intentionally use RESTRICT in the rental schema.
    # Keep rental timers/history and inventory, but stop referencing deleted chats/jobs.
    await db.execute(update(RentalLease).where(RentalLease.job_id.in_(jobs)).values(job_id=None))
    await db.execute(update(RentalLease).where(RentalLease.chat_id.in_(chats)).values(chat_id=None))
    await db.execute(update(RentalLease).where(RentalLease.funpay_account_id == ident).values(funpay_account_id=None))
    await db.execute(update(RentalAccount).where(RentalAccount.binding_id.in_(bindings)).values(binding_id=None))

    # KoSell records belong to the removed account's local order/chat history.
    # Delete their children first; no supplier cancellation/refund is performed.
    rentals = select(KosellRental.id).where(or_(KosellRental.order_id.in_(orders), KosellRental.chat_id.in_(chats)))
    await db.execute(delete(KosellMessage).where(KosellMessage.rental_id.in_(rentals)))
    await db.execute(delete(KosellOperation).where(or_(KosellOperation.rental_id.in_(rentals), KosellOperation.order_id.in_(orders))))
    await db.execute(delete(KosellRental).where(KosellRental.id.in_(rentals)))
