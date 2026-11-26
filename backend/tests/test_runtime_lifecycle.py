"""Shutdown drains every service, handles failures and cannot restart account workers."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import (
    email_codes,
    event_pipeline,
    integrated_jobs,
    lzt_market,
    main,
    reply_engine,
    runtime,
    services,
    steam_rentals,
    workers,
)
from app.automation_plugins import responder
from app.config import config
from app.funpay import FunPayError
from sqlalchemy.ext.asyncio import create_async_engine


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_drains_all_services_despite_one_failure(self):
        drained = asyncio.Event()

        async def rotation():
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()

        task = asyncio.create_task(rotation())
        await asyncio.sleep(0)
        with (patch.object(email_codes, "stop_mailbox", AsyncMock(side_effect=ValueError("private password"))),
              patch.object(lzt_market.worker, "stop", AsyncMock()) as market,
              patch.object(integrated_jobs, "stop", AsyncMock()) as jobs,
              patch.object(responder, "stop", AsyncMock()) as replies,
              patch.object(workers.workers, "begin_shutdown", Mock()) as begin,
              patch.object(workers.workers, "stop", AsyncMock()) as stop,
              patch.object(steam_rentals, "rotation_tasks", {1: task}),
              self.assertLogs("uvicorn.error", level="ERROR") as logs):
            with self.assertRaisesRegex(RuntimeError, "остановку"):
                await runtime.stop_background_tasks()
        self.assertTrue(drained.is_set())
        self.assertTrue(task.cancelled())
        begin.assert_called_once()
        for service in (market, jobs, replies, stop):
            service.assert_awaited_once()
        self.assertNotIn("private password", " ".join(logs.output))

    async def test_lifespan_cleanup_runs_after_exception_and_startup_failure(self):
        for startup_failure in (False, True):
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            stop = AsyncMock()
            try:
                with (patch.object(main, "engine", engine),
                      patch.object(config, "workers_enabled", startup_failure),
                      patch.object(main.workers, "start", AsyncMock(side_effect=RuntimeError("startup failed"))),
                      patch.object(runtime, "stop_background_tasks", stop)):
                    with self.assertRaisesRegex(RuntimeError, "startup failed" if startup_failure else "body failed"):
                        async with main.lifespan(main.app):
                            raise RuntimeError("body failed")
                stop.assert_awaited_once_with(strict=False)
            finally:
                await engine.dispose()


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_restart_does_not_duplicate_jobs(self):
        manager = workers.WorkerManager()

        async def reconcile():
            return None

        with (patch.object(reply_engine, "recover", AsyncMock()) as recover,
              patch.object(services.automation, "recover_deliveries", AsyncMock()),
              patch.object(event_pipeline, "recover", AsyncMock()),
              patch.object(manager, "reconcile", reconcile),
              patch.object(workers.accounts, "close", AsyncMock())):
            try:
                await manager.start()
                await manager.start()
                self.assertEqual(len(manager.scheduler.get_jobs()), 6)
                recover.assert_awaited_once()
                await manager.stop()
                self.assertFalse(manager.scheduler.running)
                await manager.start()
                self.assertEqual(len(manager.scheduler.get_jobs()), 6)
            finally:
                await manager.stop()

    async def test_closing_one_failed_client_still_closes_the_others(self):
        accounts = services.AccountService()
        first = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("private credentials")))
        second = SimpleNamespace(close=AsyncMock())
        accounts.clients = {1: first, 2: second}
        with self.assertRaisesRegex(FunPayError, "соединения"):
            await accounts.close()
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()
        self.assertEqual(accounts.clients, {})

    async def test_stop_continues_after_one_account_cleanup_failure(self):
        manager = workers.WorkerManager()

        async def idle():
            await asyncio.Event().wait()

        manager.tasks = {1: asyncio.create_task(idle()), 2: asyncio.create_task(idle())}
        await asyncio.sleep(0)
        second = manager.tasks[2]
        with (patch.object(workers.accounts, "discard", AsyncMock(side_effect=[RuntimeError("private"), None])) as discard,
              patch.object(workers.accounts, "close", AsyncMock()) as close):
            with self.assertRaises(RuntimeError):
                await manager.stop()
        self.assertTrue(second.cancelled())
        self.assertEqual(discard.await_count, 2)
        close.assert_awaited_once()
        self.assertEqual(manager.tasks, {})

    async def test_completed_failed_worker_does_not_block_stop_or_delete(self):
        manager = workers.WorkerManager()

        async def failed():
            raise ValueError("private error payload")

        task = asyncio.create_task(failed())
        await asyncio.sleep(0)
        manager.tasks[1] = task
        with patch.object(workers.accounts, "discard", AsyncMock()) as discard:
            with self.assertLogs("uvicorn.error", level="WARNING") as logs:
                await manager.stop_account(1)
        discard.assert_awaited_once_with(1)
        self.assertEqual(manager.health[1]["state"], "STOPPED")
        self.assertNotIn("private error payload", " ".join(logs.output))

    async def test_reconcile_cannot_restart_workers_after_shutdown_begins(self):
        manager = workers.WorkerManager()
        manager.begin_shutdown()
        with patch.object(workers, "Session", Mock(side_effect=AssertionError("must not query"))):
            await manager.reconcile()
        self.assertEqual(manager.tasks, {})

    async def test_shutdown_during_database_read_does_not_start_new_worker(self):
        manager = workers.WorkerManager()
        reading, released = asyncio.Event(), asyncio.Event()

        async def scalars(_):
            reading.set()
            await released.wait()
            return SimpleNamespace(all=lambda: [1])

        session = SimpleNamespace(scalars=scalars)
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with (patch.object(workers, "Session", Mock(return_value=context)),
              patch.object(manager, "run", AsyncMock()) as run):
            reconcile = asyncio.create_task(manager.reconcile())
            await reading.wait()
            manager.begin_shutdown()
            released.set()
            await asyncio.wait_for(reconcile, timeout=2)
        run.assert_not_called()
        self.assertEqual(manager.tasks, {})

    async def test_reconcile_waits_for_account_stop_to_drain(self):
        manager = workers.WorkerManager()
        closing, released = asyncio.Event(), asyncio.Event()

        async def discard(_):
            closing.set()
            await released.wait()

        session = AsyncMock()
        session.scalars.return_value = SimpleNamespace(all=lambda: [])
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with (patch.object(workers.accounts, "discard", discard),
              patch.object(workers, "Session", Mock(return_value=context)) as database):
            stop = asyncio.create_task(manager.stop_account(1))
            await closing.wait()
            reconcile = asyncio.create_task(manager.reconcile())
            await asyncio.sleep(0)
            database.assert_not_called()
            released.set()
            await asyncio.wait_for(asyncio.gather(stop, reconcile), timeout=2)
        self.assertEqual(manager.tasks, {})
