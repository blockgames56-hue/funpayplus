"""Local startup checks; no real database migration or business workers."""

import contextlib
import io
import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import launcher
from alembic import command
from app.config import config


@contextlib.contextmanager
def fake_backend(schema=None, *, token="test-key", healthy=True, redirect=False):
    calls = []
    if schema is None:
        schema = {"info": {"title": "FunPay Plus"}, "paths": {"/api/health": {}}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            if redirect:
                self.send_response(302)
                self.send_header("Location", "/redirect-must-not-be-followed")
                self.end_headers()
                return
            if self.path == "/openapi.json":
                result, status = schema, 200
            elif self.headers.get("Authorization") != "Bearer " + token:
                result, status = {"detail": "Unauthorized"}, 401
            else:
                result, status = {"ok": healthy, "database": "sqlite-wal"}, 200
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(config, "api_token", "test-key"):
            yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ProbeTests(unittest.TestCase):
    def test_healthy_server_with_same_key_is_reused(self):
        with fake_backend() as (port, calls):
            self.assertEqual(launcher.probe_server(port=port), 0)
            self.assertEqual([p for p, _ in calls], ["/openapi.json", "/api/health"])
            self.assertIsNone(calls[0][1])

    def test_foreign_service_does_not_receive_store_key(self):
        with fake_backend({"info": {"title": "Other app"}}) as (port, calls):
            self.assertEqual(launcher.probe_server(port=port), 3)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(calls[0][1])

    def test_other_store_is_not_reused(self):
        with fake_backend(token="another-store") as (port, _calls):
            self.assertEqual(launcher.probe_server(port=port), 3)

    def test_unhealthy_server_is_not_reused(self):
        with fake_backend(healthy=False) as (port, _calls):
            self.assertEqual(launcher.probe_server(port=port), 3)

    def test_redirect_is_not_followed(self):
        with fake_backend(redirect=True) as (port, calls):
            self.assertEqual(launcher.probe_server(port=port), 3)
            self.assertEqual(len(calls), 1)

    def test_malformed_schema_is_treated_as_busy(self):
        for schema in ([], {"info": None}, {"info": {"title": "FunPay Plus"}, "paths": None}):
            with fake_backend(schema) as (port, _calls):
                self.assertEqual(launcher.probe_server(port=port), 3)

    def test_free_port_is_reported_without_loading_app(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.assertEqual(launcher.probe_server(port=port), 2)


class ReservationTests(unittest.TestCase):
    def test_bound_port_cannot_start_second_backend(self):
        with launcher.reserve_socket("127.0.0.1", 0) as first:
            with self.assertRaises(OSError):
                launcher.reserve_socket("127.0.0.1", first.getsockname()[1])

    def test_bind_failure_never_imports_or_starts_workers(self):
        uvicorn = SimpleNamespace(Config=Mock(), Server=Mock())
        with (
            patch.object(sys, "argv", ["launcher.py"]),
            patch.object(launcher, "reserve_socket", side_effect=OSError("busy")),
            patch.dict(sys.modules, {"uvicorn": uvicorn}),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(launcher.main(), 1)
        uvicorn.Config.assert_not_called()
        uvicorn.Server.assert_not_called()

    def test_port_is_reserved_before_migration_and_socket_is_closed(self):
        reserve = launcher.reserve_socket
        listener = launcher.reserve_socket("127.0.0.1", 0)
        fake_server = Mock()

        def migrated(*_args):
            self.assertGreater(listener.getsockname()[1], 0)
            with self.assertRaises(OSError):
                reserve("127.0.0.1", listener.getsockname()[1])

        upgrade = Mock(side_effect=migrated)
        uvicorn = SimpleNamespace(Config=Mock(), Server=Mock(return_value=fake_server))
        with (
            patch.object(sys, "argv", ["launcher.py"]),
            patch.object(launcher, "reserve_socket", return_value=listener),
            patch.dict(
                sys.modules, {"uvicorn": uvicorn, "app.main": SimpleNamespace(app=object())}
            ),
            patch.object(command, "upgrade", upgrade),
        ):
            self.assertEqual(launcher.main(), 0)
        upgrade.assert_called_once()
        fake_server.run.assert_called_once_with(sockets=[listener])
        self.assertEqual(listener.fileno(), -1)
