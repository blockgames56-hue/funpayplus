"""Drain all runtime tasks before closing network clients or database connections."""

import asyncio
import logging


async def stop_background_tasks(*, strict=True):
    from . import email_codes, integrated_jobs, lzt_market, steam_rentals
    from .automation_plugins import responder
    from .workers import workers

    workers.begin_shutdown()
    from . import kosellrent, kosellrent_api

    await kosellrent_api.stop_operation()
    await kosellrent.stop()
    rotations = list(steam_rentals.rotation_tasks.values())
    for task in rotations:
        task.cancel()
    results = await asyncio.gather(
        lzt_market.worker.stop(), email_codes.stop_mailbox(), integrated_jobs.stop(),
        responder.stop(), *rotations, return_exceptions=True,
    )
    try:
        await workers.stop()
    except Exception as error:
        results.append(error)
    errors = [error for error in results if isinstance(error, Exception)]
    if errors:
        # Exception bodies can contain remote responses and private credentials.
        logging.getLogger("uvicorn.error").error(
            "Background shutdown failed: %s", ", ".join(type(error).__name__ for error in errors)
        )
        if strict:
            raise RuntimeError("Не все фоновые задачи подтвердили остановку. Проверьте журнал.")
