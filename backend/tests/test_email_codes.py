import asyncio
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

from app import email_codes
from app.crypto import Vault


def mail(body="A7BC9", sender="noreply@steampowered.com", age=0):
    from datetime import timedelta

    message = EmailMessage()
    message["From"] = sender
    message["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=age))
    message.set_content(f"Steam Guard\n\n{body}\n\nSample store")
    return message.as_bytes()


class ParserTests(unittest.TestCase):
    def test_extracts_alphanumeric_guard(self):
        self.assertEqual(email_codes.steam_code(mail()), "A7BC9")

    def test_rejects_stale_wrong_sender_and_ambiguous(self):
        for raw in (
            mail(age=700),
            mail(sender="noreply@steampowered.com.attacker.test"),
            mail(body="A7BC9\n12345"),
        ):
            self.assertIsNone(email_codes.steam_code(raw))

    def test_html_alternative(self):
        message = EmailMessage()
        message["From"] = "noreply@steampowered.com"
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message.set_content("<div>Steam Guard</div><div>A7BC9</div>", subtype="html")
        self.assertEqual(email_codes.steam_code(message.as_bytes()), "A7BC9")

    def test_epic_code_checks_sender_and_recency(self):
        self.assertEqual(
            email_codes.epic_code(mail("123456", sender="help@accts.epicgames.com")), "123456"
        )
        self.assertIsNone(email_codes.epic_code(mail("123456", sender="attacker.example")))

    def test_credentials_are_not_in_repr(self):
        config = email_codes.MailboxConfig("sample", "imap.example.com", "user", "secret-sample")
        self.assertNotIn("secret-sample", repr(config))


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mailbox_password_is_encrypted_in_settings(self):
        config = email_codes.MailboxConfig(
            "sample", "imap.example.com", "user@example.com", "app-secret"
        )
        with (
            patch.object(email_codes, "mail_vault", return_value=Vault("test-key")),
            patch.object(email_codes, "save_setting", AsyncMock()) as save,
            patch.object(email_codes, "get_setting", AsyncMock()) as get,
        ):
            await email_codes.save_mailbox(config)
            stored = save.call_args.args[1]
            self.assertNotIn("app-secret", str(stored))
            get.return_value = stored
            loaded = await email_codes.load_mailbox("sample")
            self.assertEqual(loaded.password, "app-secret")

    async def test_reconnect_recovers_new_uid_and_emits(self):
        config = email_codes.MailboxConfig("sample", "imap.example.com", "user", "secret")
        attempts = 0
        raw = mail()

        class Client:
            def select_folder(self, *args, **kwargs):
                return {b"UIDVALIDITY": 1, b"UIDNEXT": 3}

            def search(self, criteria):
                return [1, 2]  # UID below cursor must be ignored.

            def fetch(self, uids, fields):
                head, body = raw.split(b"\n\n", 1)
                return {
                    2: {
                        b"RFC822.SIZE": len(raw),
                        b"BODY[HEADER]": head + b"\n",
                        b"BODY[TEXT]": body,
                    }
                }

            def idle(self):
                pass

            def idle_check(self, timeout):
                fetcher._stop.set()
                return []

            def idle_done(self):
                pass

            def shutdown(self):
                pass

        def connector(_config):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("disconnected")
            return Client()

        fetcher = email_codes.EmailCodeFetcher(config, connector=connector)
        with (
            patch.object(
                email_codes, "get_setting", AsyncMock(return_value={"validity": 1, "uid": 1})
            ),
            patch.object(email_codes, "save_setting", AsyncMock()) as save,
            patch.object(email_codes.bus, "log", AsyncMock()),
            patch.object(email_codes.bus, "emit", AsyncMock()) as emit,
        ):
            await fetcher.start()
            try:
                await asyncio.wait_for(asyncio.to_thread(fetcher._thread.join), timeout=30)
            finally:
                await fetcher.stop()
            self.assertEqual(attempts, 2)
            emit.assert_awaited_once()
            self.assertEqual(emit.call_args.args[0], "new_2fa_code")
            self.assertEqual(emit.call_args.args[1]["code"], "A7BC9")
            self.assertEqual(save.call_args.args[1]["uid"], 2)
