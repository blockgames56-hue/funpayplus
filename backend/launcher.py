"""Frozen sidecar entrypoint: migrate, then start one ASGI process."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_server(host="127.0.0.1", port=8765):
    """0: this store is healthy; 2: port free; 3: busy/unverified.

    Check the public API identity before sending the store's credential. Never
    follow redirects or use environment proxies for this local health check.
    """
    target = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        with reserve_socket(host, port):
            pass
        return 2
    except OSError:
        pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    base = f"http://{target}:{port}"
    try:
        with opener.open(base + "/openapi.json", timeout=2) as response:
            schema = json.loads(response.read(2 * 1024 * 1024))
        if (
            not isinstance(schema, dict)
            or not isinstance(schema.get("info"), dict)
            or schema["info"].get("title") != "FunPay Plus"
            or not isinstance(schema.get("paths"), dict)
            or "/api/health" not in schema["paths"]
        ):
            return 3
        from app.config import config

        request = urllib.request.Request(
            base + "/api/health", headers={"Authorization": "Bearer " + config.api_token}
        )
        with opener.open(request, timeout=2) as response:
            health = json.loads(response.read(65536))
        return (
            0
            if isinstance(health, dict) and health.get("ok") is True and "database" in health
            else 3
        )
    except (OSError, ValueError, ImportError):
        return 3


def reserve_socket(host, port):
    """Reserve the address before migrations or application startup handlers."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(128)
        return listener
    except BaseException:
        listener.close()
        raise


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        return probe_server(args.host, args.port)
    try:
        listener = reserve_socket(args.host, args.port)
    except OSError:
        print(
            f"Cannot start FunPay Plus: {args.host}:{args.port} is already in use or unavailable. Run start.cmd to connect to an existing instance.",
            file=sys.stderr,
        )
        return 1
    with listener:
        import uvicorn
        from alembic import command
        from alembic.config import Config

        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        migration = Config()
        migration.set_main_option("script_location", str(root / "migrations"))
        command.upgrade(migration, "head")
        from app.main import app

        server = uvicorn.Server(
            uvicorn.Config(
                app, host=args.host, port=args.port, workers=1, access_log=False, ws_max_size=65536
            )
        )
        server.run(sockets=[listener])
    return 0


if __name__ == "__main__":
    sys.exit(main())
