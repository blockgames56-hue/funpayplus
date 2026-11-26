"""Domain-free installation: real app API, fake external APIs and isolated shell tools."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

temporary = tempfile.TemporaryDirectory(prefix="fp-easy-vps-", ignore_cleanup_errors=True)
os.environ.update(
    STORAGE_DIR=temporary.name,
    DATABASE_URL="sqlite+aiosqlite:///" + (Path(temporary.name) / "test.db").as_posix(),
    WORKERS_ENABLED="false",
    API_TOKEN="easy-vps-test-access-token-1234567890",
    SECRET_KEY="easy-vps-test-encryption-key-1234567890",
)

import easy_setup  # noqa: E402
import httpx  # noqa: E402
from app import main  # noqa: E402
from app import telegram_control as tc  # noqa: E402
from app.config import config  # noqa: E402
from app.db import Session, engine  # noqa: E402
from app.funpay import FunPayError  # noqa: E402
from app.models import Account, Base  # noqa: E402
from app.security import vault  # noqa: E402
from app.services import get_setting, telegram  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import select  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:synthetic_setup_token"
GOLDEN = "synthetic-golden-cookie"
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"


class EasySetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.drop_all)
            await db.run_sync(Base.metadata.create_all)

        async def bot_api(token, method, payload):
            if token == "999:invalid":
                raise FunPayError("invalid Telegram token")
            if method == "getMe":
                return {"id": 123456, "username": "SyntheticSetupBot"}
            if method == "sendMessage":
                return {"message_id": 1}
            return True

        self.bot = patch.object(telegram, "request", AsyncMock(side_effect=bot_api))
        self.remote = self.bot.start()
        self.client = Mock(
            sync_profile=AsyncMock(return_value={"funpay_id": 999, "username": "TestStore"}),
            close=AsyncMock(),
        )
        self.funpay = patch.object(main, "FunPayClient", return_value=self.client)
        self.funpay.start()
        self.transport = httpx.ASGITransport(app=main.app)

    async def asyncTearDown(self):
        self.bot.stop()
        self.funpay.stop()
        await engine.dispose()

    async def test_one_token_configures_notifications_ip_mini_and_pairing(self):
        result = await easy_setup.configure(TOKEN, "", "https://8.8.8.8", transport=self.transport)
        self.assertFalse(result["errors"])
        self.assertTrue(result["pair_url"].startswith("https://t.me/SyntheticSetupBot?start=pair_"))
        cfg = await get_setting("telegram")
        self.assertTrue(cfg["enabled"])
        self.assertTrue(cfg["control_enabled"])
        self.assertEqual(cfg["mini_app_url"], "https://8.8.8.8/telegram")
        self.assertEqual(vault.decrypt(cfg["token"]), TOKEN)
        self.assertIsNone(await get_setting("telegram_owner"))
        self.assertTrue(await get_setting("telegram_public_lock"))
        self.assertNotIn(TOKEN, json.dumps(result))
        nonce = result["pair_url"].split("pair_")[1]
        await tc.control.bind(nonce, {"id": 42, "first_name": "Owner"}, cfg, TOKEN)
        self.assertEqual((await get_setting("telegram"))["chat_id"], "42")

    async def test_optional_funpay_account_is_added_to_server(self):
        result = await easy_setup.configure(TOKEN, GOLDEN, "", transport=self.transport)
        self.assertFalse(result["errors"])
        self.assertTrue(result["account_added"])
        async with Session() as db:
            account = await db.scalar(select(Account))
            self.assertEqual(account.username, "TestStore")
            self.assertEqual(vault.decrypt(account.golden_key), GOLDEN)
        self.assertEqual((await get_setting("telegram"))["mini_app_url"], "")

    async def test_blank_credentials_skip_without_creating_accounts(self):
        result = await easy_setup.configure("", "", None, transport=self.transport)
        self.assertTrue(result["telegram_skipped"])
        self.assertFalse(result["account_added"])
        self.remote.assert_not_awaited()
        self.client.sync_profile.assert_not_awaited()

    async def test_existing_token_is_reused_without_printing_it(self):
        await easy_setup.configure(TOKEN, "", "", transport=self.transport)
        result = await easy_setup.configure("", "", None, transport=self.transport)
        self.assertFalse(result["errors"])
        self.assertTrue(result["pair_url"])
        self.assertFalse(result["telegram_skipped"])
        self.assertEqual(vault.decrypt((await get_setting("telegram"))["token"]), TOKEN)

    async def test_existing_account_is_not_duplicated_on_retry(self):
        await easy_setup.configure(TOKEN, GOLDEN, "", transport=self.transport)
        result = await easy_setup.configure("", GOLDEN, "", transport=self.transport)
        self.assertFalse(result["errors"])
        self.assertTrue(result["account_added"])
        async with Session() as db:
            self.assertEqual(len(list((await db.scalars(select(Account))).all())), 1)

    async def test_invalid_bot_does_not_erase_existing_token(self):
        await easy_setup.configure(TOKEN, "", "", transport=self.transport)
        result = await easy_setup.configure("999:invalid", "", None, transport=self.transport)
        self.assertTrue(result["errors"])
        self.assertNotIn("999:invalid", json.dumps(result))
        self.assertEqual(vault.decrypt((await get_setting("telegram"))["token"]), TOKEN)

    async def test_failed_https_can_clear_mini_but_keep_bot(self):
        await easy_setup.configure(TOKEN, "", "https://8.8.8.8", transport=self.transport)
        result = await easy_setup.configure("", "", "", transport=self.transport)
        self.assertFalse(result["errors"])
        self.assertTrue(result["pair_url"])
        cfg = await get_setting("telegram")
        self.assertEqual(cfg["mini_app_url"], "")
        self.assertTrue(cfg["control_enabled"])
        self.assertTrue(await get_setting("telegram_public_lock"))

    async def test_first_public_boot_is_locked_before_owner_is_paired(self):
        with patch.object(config, "telegram_only", True):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app, client=("203.0.113.8", 123)),
                base_url="http://testserver",
            ) as visitor:
                result = await visitor.post(
                    "/api/auth/connect", headers={"X-Forwarded-For": "127.0.0.1"}
                )
                self.assertEqual(result.status_code, 403)
                result = await visitor.get("/api/accounts")
                self.assertEqual(result.status_code, 401)
        await easy_setup.configure(TOKEN, "", "", transport=self.transport)

    async def test_transport_failure_has_no_secret_in_error(self):
        async def failed(request):
            raise httpx.ConnectError("private " + TOKEN)

        result = await easy_setup.configure(TOKEN, "", None, transport=httpx.MockTransport(failed))
        self.assertTrue(result["errors"])
        self.assertNotIn(TOKEN, json.dumps(result))


class ConsoleTests(unittest.TestCase):
    def test_credentials_are_read_from_stdin_and_never_printed(self):
        fake = AsyncMock(
            return_value={
                "pair_url": "https://t.me/Test?start=pair_once",
                "account_added": True,
                "telegram_skipped": False,
                "errors": [],
            }
        )
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(easy_setup, "configure", fake),
            patch.object(sys, "argv", ["easy_setup.py", "--mini-app", "https://8.8.8.8"]),
            patch.object(sys, "stdin", io.StringIO(TOKEN + "\n" + GOLDEN + "\n")),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            self.assertEqual(easy_setup.main(), 0)
        fake.assert_awaited_once_with(TOKEN, GOLDEN, "https://8.8.8.8")
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        self.assertNotIn(GOLDEN, out.getvalue() + err.getvalue())


@unittest.skipUnless(BASH and Path(BASH).exists(), "Bash required")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fp-install-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "deploy").mkdir()
        self.tools = self.root / "bin"
        self.tools.mkdir()
        for file in ["setup.sh", "check-project.sh"]:
            shutil.copyfile(ROOT / "deploy" / file, self.root / "deploy" / file)
        (self.root / "docker-compose.yml").write_text("services: {}")
        self.docker_log = self.root / "docker.log"
        self.telegram_log = self.root / "telegram.log"
        self.curl_log = self.root / "curl.log"
        scripts = {
            "id": "echo 0",
            "systemctl": "exit 0",
            "openssl": "printf '%064d\n' 1",
            "curl": 'echo called >> "$MOCK_CURL_LOG"; printf "%s" "${MOCK_IP:-8.8.8.8}"',
            "docker": 'printf "%s\n" "$*" >> "$MOCK_DOCKER_LOG"; if [[ ${MOCK_CORE_FAIL:-0} == 1 && "$*" == *"up -d"* && "$*" == *"backend frontend"* ]]; then exit 99; fi; if [[ ${MOCK_CADDY_FAIL:-0} == 1 && "$*" == *"up -d"* && "$*" == *caddy* ]]; then exit 55; fi; if [[ ${MOCK_TLS_FAIL:-0} == 1 && "$*" == *check_public.py* ]]; then exit 9; fi',
        }
        for name, body in scripts.items():
            p = self.tools / name
            p.write_text("#!/usr/bin/env bash\n" + body + "\n", newline="\n")
            p.chmod(0o755)
        p = self.root / "deploy/connect-telegram.sh"
        p.write_text(
            '#!/usr/bin/env bash\nprintf "<%s>\n" "$@" > "$MOCK_TELEGRAM_LOG"\n', newline="\n"
        )
        self.env = {
            **os.environ,
            "MOCK_BIN": self.tools.as_posix(),
            "MOCK_DOCKER_LOG": self.docker_log.as_posix(),
            "MOCK_TELEGRAM_LOG": self.telegram_log.as_posix(),
            "MOCK_CURL_LOG": self.curl_log.as_posix(),
        }

    def run_setup(self, *args, **env):
        command = 'export PATH="$MOCK_BIN:$PATH"; bash deploy/setup.sh "$@"'
        if os.name == "nt":
            command = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; bash deploy/setup.sh "$@"'
        return subprocess.run(
            [BASH, "-c", command, "setup", *args],
            cwd=self.root,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_easy_setup_detects_ip_and_wires_https_and_bot(self):
        result = self.run_setup("--easy")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = dotenv_values(self.root / ".env")
        self.assertEqual(env["PUBLIC_IP"], "8.8.8.8")
        self.assertEqual(env["PUBLIC_ORIGIN"], "https://8.8.8.8")
        self.assertEqual(env["TELEGRAM_ONLY"], "true")
        self.assertIn("compose.ip.yml", self.docker_log.read_text())
        self.assertEqual(
            self.telegram_log.read_text().splitlines(), ["<--mini-app>", "<https://8.8.8.8>"]
        )
        again = self.run_setup("--easy")
        self.assertEqual(again.returncode, 0, again.stderr)
        after = dotenv_values(self.root / ".env")
        for key in ["SECRET_KEY", "API_TOKEN", "DB_PASSWORD"]:
            self.assertEqual(env[key], after[key])

    def test_https_failure_keeps_services_running_and_configures_buttons(self):
        result = self.run_setup("--easy", MOCK_TLS_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.telegram_log.read_text().splitlines(), ["<--mini-app>", "<>"])
        commands = self.docker_log.read_text()
        self.assertIn("up -d", commands)
        self.assertNotIn(" stop ", commands)
        self.assertIn("80/443", result.stderr)

    def test_bot_only_does_not_detect_ip_or_start_caddy(self):
        result = self.run_setup("--bot-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertNotIn("PUBLIC_IP", dotenv_values(self.root / ".env"))
        self.assertNotIn("compose.ip.yml", self.docker_log.read_text())
        self.assertEqual(self.telegram_log.read_text().splitlines(), ["<--mini-app>", "<>"])

    def test_ip_detection_failure_falls_back_without_publishing_http(self):
        result = self.run_setup("--easy", MOCK_IP="not-an-ip")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = dotenv_values(self.root / ".env")
        self.assertEqual(env["BIND_ADDRESS"], "127.0.0.1")
        self.assertEqual(env["TELEGRAM_ONLY"], "true")
        self.assertNotIn("PUBLIC_IP", env)

    def test_private_or_malformed_explicit_ip_is_rejected(self):
        for ip in [
            "127.0.0.1",
            "10.1.2.3",
            "192.168.1.1",
            "172.20.1.1",
            "100.64.0.1",
            "999.1.1.1",
            "1.2.3.04",
            "8.8.8.8;echo x",
        ]:
            result = self.run_setup("--ip", ip)
            self.assertNotEqual(result.returncode, 0, ip)
        self.assertNotIn("up -d", self.docker_log.read_text())

    def test_manual_ip_and_prepare_only_do_not_call_network(self):
        result = self.run_setup("--prepare-only", "--ip", "1.1.1.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertNotIn("up -d", self.docker_log.read_text())
        self.assertEqual(dotenv_values(self.root / ".env")["PUBLIC_IP"], "1.1.1.1")

    def test_existing_domain_is_preserved(self):
        self.run_setup("--prepare-only", "--domain", "panel.example.com")
        result = self.run_setup("--easy")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = dotenv_values(self.root / ".env")
        self.assertEqual(env["DOMAIN"], "panel.example.com")
        self.assertFalse(self.curl_log.exists())
        self.assertNotIn("PUBLIC_IP", env)
        self.assertEqual(
            self.telegram_log.read_text().splitlines(),
            ["<--mini-app>", "<https://panel.example.com>"],
        )

    def test_caddy_ip_uses_public_issuer_profile_sni_and_persistent_storage(self):
        caddy = (ROOT / "deploy/Caddyfile.ip").read_text()
        compose = (ROOT / "deploy/compose.ip.yml").read_text()
        self.assertIn("profile shortlived", caddy)
        self.assertIn("default_sni {$PUBLIC_IP}", caddy)
        self.assertNotIn("tls internal", caddy)
        self.assertIn("acme-v02.api.letsencrypt.org/directory", caddy)
        self.assertIn("caddy_data:/data", compose)
        self.assertIn("unless-stopped", compose)
        self.assertIn("TELEGRAM_ONLY:", (ROOT / "docker-compose.yml").read_text())

    def test_busy_caddy_ports_do_not_stop_bot_setup(self):
        result = self.run_setup("--easy", MOCK_CADDY_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.telegram_log.read_text().splitlines(), ["<--mini-app>", "<>"])
        self.assertIn("80/443", result.stderr)

    def test_core_start_failure_is_not_reported_as_success(self):
        result = self.run_setup("--easy", MOCK_CORE_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.telegram_log.exists())

    def test_piped_installer_uses_official_repo_and_resumes_known_checkout(self):
        destination = self.root / "checkout"
        clone_log = self.root / "clone.log"
        setup_log = self.root / "setup.log"
        git = self.tools / "git"
        git.write_text(
            """#!/usr/bin/env bash
if [[ "$*" == *"config --get remote.origin.url"* ]]; then echo https://github.com/blockgames56-hue/funpayplus.git; exit 0; fi
if [[ "$1" == clone ]]; then
  printf "%s\n" "$*" >> "$MOCK_CLONE_LOG"
  destination="${@: -1}"
  mkdir -p "$destination/deploy" "$destination/.git"
  printf 'services: {}' > "$destination/docker-compose.yml"
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" > "$MOCK_SETUP_LOG"\n' > "$destination/deploy/setup.sh"
  exit 0
fi
exit 1
""",
            newline="\n",
        )
        git.chmod(0o755)
        prefix = 'export PATH="$MOCK_BIN:$PATH"; destination="$MOCK_DEST"; '
        if os.name == "nt":
            prefix = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; destination=$(cygpath -u "$MOCK_DEST"); '
        command = prefix + 'cat "$MOCK_INSTALL" | bash -s -- --easy --dir "$destination"'
        env = {
            **self.env,
            "MOCK_INSTALL": (ROOT / "install.sh").as_posix(),
            "MOCK_DEST": destination.as_posix(),
            "MOCK_CLONE_LOG": clone_log.as_posix(),
            "MOCK_SETUP_LOG": setup_log.as_posix(),
        }
        for _ in range(2):
            result = subprocess.run(
                [BASH, "-c", command], cwd=self.root, env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(clone_log.read_text().splitlines()), 1)
        self.assertIn("https://github.com/blockgames56-hue/funpayplus.git", clone_log.read_text())
        self.assertEqual(setup_log.read_text().strip(), "--easy")
