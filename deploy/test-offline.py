"""Run parser, validation and encryption tests without touching FunPay.

--no-sockets is for execution sandboxes that deny even asyncio's local socketpair.
It supplies a socket-free event loop only for these MockTransport tests.
"""

import argparse
import asyncio
import sys
import unittest
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-sockets", action="store_true")
    parser.add_argument("--pattern", default="", help="Run one test file, e.g. test_protocol.py")
    args = parser.parse_args()
    if args.no_sockets:

        class OfflineLoop(asyncio.SelectorEventLoop):
            def _make_self_pipe(self):
                self._ssock = self._csock = None
                self._selector.select = lambda timeout=None: []

            def _close_self_pipe(self):
                self._ssock = self._csock = None

            def _write_to_self(self):
                return None

        class OfflinePolicy(asyncio.DefaultEventLoopPolicy):
            def new_event_loop(self):
                return OfflineLoop()

        asyncio.set_event_loop_policy(OfflinePolicy())
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    patterns = (args.pattern,) if args.pattern else ("test_protocol.py", "test_validation.py", "test_loyalty.py", "test_avatars.py", "test_blacklist_hooks.py", "test_automation_plugins.py", "test_email_codes.py")
    for pattern in patterns:
        suite.addTests(loader.discover(str(root / "backend/tests"), pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())


if __name__ == "__main__":
    main()
