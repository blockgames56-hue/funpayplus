"""Portable credentials, lossless database transfer and the server installer contract."""

import asyncio
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from app.crypto import Vault
from app.models import Account, Base, Settings
from dotenv import dotenv_values
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def module_from(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = module_from("export_transfer", PROJECT / "deploy/export_transfer.py")
importer = module_from("import_snapshot", ROOT / "import_snapshot.py")
packager = module_from("package_source", PROJECT / "deploy/package-source.py")
public_check = module_from("check_public", ROOT / "check_public.py")


class PublicPanelTests(unittest.TestCase):
    def test_https_health_response_is_verified(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"ok": true, "database": "postgresql"}')
        with patch.object(public_check.urllib.request, "build_opener", return_value=opener):
            public_check.verify("https://panel.example.com", "synthetic-token", timeout=0)
        self.assertEqual(opener.open.call_args.args[0].full_url, "https://panel.example.com/api/health")

    def test_html_or_wrong_application_is_not_success(self):
        for body in (b'<html>proxy</html>', b'[]', b'{"ok": true}'):
            opener = Mock()
            opener.open.return_value = io.BytesIO(body)
            with patch.object(public_check.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(RuntimeError):
                    public_check.verify("https://panel.example.com", "synthetic-token", timeout=0)
        self.assertIsNone(public_check.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="funpay-transfer-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.storage = self.folder / "storage"
        self.storage.mkdir()
        self.master = "synthetic-master-key-with-at-least-32-characters"
        self.mail_key = "synthetic-separate-mailbox-key-for-transfer"
        (self.storage / "encryption.key").write_text(self.master, encoding="utf-8")
        self.source_engine = create_engine("sqlite:///" + (self.storage / "pulse.db").as_posix())
        Base.metadata.create_all(self.source_engine)
        with Session(self.source_engine) as db:
            db.add(Account(title="Test seller", username="Seller", user_agent="offline-test",
                           golden_key=Vault(self.master).encrypt("synthetic-funpay-cookie")))
            db.add(Settings(key="imap_mailbox_default", value=json.dumps({"ident": "default", "host": "mail.example",
                "password": Vault(self.mail_key).encrypt("synthetic-mail-password"), "proxy_password": ""})))
            db.add(Settings(key="plugin_controls", value='{"responder": true}'))
            db.commit()
        self.source_engine.dispose()

    def test_export_rewraps_mail_without_changing_source_or_exposing_plaintext(self):
        before = (self.storage / "pulse.db").read_bytes()
        output = self.folder / "export"
        exporter.export_snapshot(self.storage, output, {}, mailbox_key=self.mail_key)
        values = dotenv_values(output / "server.env")
        self.assertEqual(values["SECRET_KEY"], self.master)
        self.assertGreaterEqual(len(values["API_TOKEN"]), 24)
        with closing(sqlite3.connect(output / "snapshot.db")) as db:
            data = json.loads(db.execute("SELECT value FROM settings WHERE key='imap_mailbox_default'").fetchone()[0])
            self.assertEqual(Vault(values["SECRET_KEY"]).decrypt(data["password"]), "synthetic-mail-password")
            cookie = db.execute("SELECT golden_key FROM accounts").fetchone()[0]
            self.assertEqual(Vault(values["SECRET_KEY"]).decrypt(cookie), "synthetic-funpay-cookie")
        self.assertEqual(before, (self.storage / "pulse.db").read_bytes())
        self.assertNotIn(b"synthetic-mail-password", (output / "snapshot.db").read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_windows_protected_key_survives_export(self):
        from app.os_secrets import dpapi

        (self.storage / "encryption.key").unlink()
        sealed = dpapi(self.master.encode())
        (self.storage / "encryption.key.dpapi").write_bytes(sealed)
        output = self.folder / "export"
        exporter.export_snapshot(self.storage, output, {}, mailbox_key=self.mail_key)
        self.assertEqual(dotenv_values(output / "server.env")["SECRET_KEY"], self.master)
        self.assertEqual((self.storage / "encryption.key.dpapi").read_bytes(), sealed)
        self.assertFalse((self.storage / "encryption.key").exists())

    def test_wrong_master_or_database_is_rejected(self):
        with self.assertRaises(Exception):
            exporter.export_snapshot(self.storage, self.folder / "wrong-key", {"SECRET_KEY": "wrong" * 12}, mailbox_key=self.mail_key)
        with self.assertRaisesRegex(ValueError, "другую базу"):
            exporter.export_snapshot(self.storage, self.folder / "wrong-db", {"DATABASE_URL": "sqlite:///different.db"})

    def test_import_preserves_rows_and_refuses_any_nonempty_target(self):
        async def check():
            target = create_async_engine("sqlite+aiosqlite:///" + (self.folder / "target.db").as_posix())
            try:
                async with target.begin() as db:
                    await db.run_sync(Base.metadata.create_all)
                await importer.import_snapshot(str(self.storage / "pulse.db"), target)
                async with target.connect() as db:
                    row = (await db.execute(select(Account.__table__))).mappings().one()
                    self.assertEqual(Vault(self.master).decrypt(row["golden_key"]), "synthetic-funpay-cookie")
                    self.assertEqual(len((await db.execute(select(Settings.key))).all()), 2)
                with self.assertRaisesRegex(RuntimeError, "уже содержит"):
                    await importer.import_snapshot(str(self.storage / "pulse.db"), target)
            finally:
                await target.dispose()
        asyncio.run(check())

    def test_headless_mail_vault_uses_portable_master(self):
        from app.config import config
        from app.email_codes import mail_vault

        with patch.object(config, "mailbox_key_store", "master"), patch.object(config, "secret_key", self.master):
            self.assertEqual(mail_vault().decrypt(Vault(self.master).encrypt("mail-secret")), "mail-secret")

    def test_old_schema_is_upgraded_only_in_private_copy(self):
        original = self.folder / "old.db"
        upgraded = self.folder / "upgraded.db"
        env = {**os.environ, "STORAGE_DIR": str(self.folder / "migration-config"),
               "SECRET_KEY": self.master, "API_TOKEN": "synthetic-migration-access-token",
               "DATABASE_URL": "sqlite+aiosqlite:///" + original.as_posix(), "WORKERS_ENABLED": "false"}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0019"], cwd=ROOT, env=env, check=True, capture_output=True)
        with patch.dict(os.environ, env):
            importer.prepare_snapshot(original, upgraded)
        with closing(sqlite3.connect(original)) as db:
            self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0019")
        with closing(sqlite3.connect(upgraded)) as db:
            self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0021")


class SourceArchiveTests(unittest.TestCase):
    def test_archive_excludes_data_keys_and_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("install.sh", ".env.example", ".env", "storage/pulse.db", "backend/local.db", "secret.key.dpapi", "node_modules/dep/index.js", "apps/web/src/main.tsx"):
                file = folder / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("sample")
            self.assertEqual({name for _, name in packager.source_files(folder)}, {"install.sh", ".env.example", "apps/web/src/main.tsx"})


BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"


@unittest.skipUnless(BASH and Path(BASH).exists(), "Bash required")
class InstallerTests(unittest.TestCase):
    def test_other_installation_cannot_reuse_docker_project(self):
        with tempfile.TemporaryDirectory(prefix="funpay-project-guard-") as directory:
            folder = Path(directory)
            (folder / "deploy").mkdir()
            shutil.copy(PROJECT / "deploy/check-project.sh", folder / "deploy/check-project.sh")
            (folder / ".env").write_text("COMPOSE_PROJECT_NAME=test-project\n")
            tools = folder / "bin"
            tools.mkdir()
            docker = tools / "docker"
            docker.write_text('#!/usr/bin/env bash\nif [[ $1 == ps ]]; then echo synthetic-container; else echo "$MOCK_OWNER"; fi\n', newline="\n")
            docker.chmod(0o755)
            command = 'export PATH="$MOCK_BIN:$PATH"; bash deploy/check-project.sh'
            if os.name == "nt":
                command = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; bash deploy/check-project.sh'
            env = {**os.environ, "COMPOSE_PROJECT_NAME": "test-project", "MOCK_BIN": tools.as_posix()}
            bad = subprocess.run([BASH, "-c", command], cwd=folder,
                env={**env, "MOCK_OWNER": (folder / 'another-installation').as_posix()}, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            good = subprocess.run([BASH, "-c", command], cwd=folder,
                env={**env, "MOCK_OWNER": folder.as_posix()}, capture_output=True)
            self.assertEqual(good.returncode, 0, good.stderr)

    def test_bootstrap_clones_and_passes_domain_without_overwriting(self):
        with tempfile.TemporaryDirectory(prefix="funpay-bootstrap-") as directory:
            folder = Path(directory)
            tools = folder / "bin"
            tools.mkdir()
            setup = folder / "fake-setup.sh"
            setup.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$MOCK_SETUP_LOG"\n', newline="\n")
            scripts = {
                "id": "echo 0\n",
                "git": 'destination="${@: -1}"\nmkdir -p "$destination/deploy"\ncp "$MOCK_SETUP" "$destination/deploy/setup.sh"\ntouch "$destination/docker-compose.yml"\n',
            }
            for name, body in scripts.items():
                file = tools / name
                file.write_text("#!/usr/bin/env bash\n" + body, newline="\n")
                file.chmod(0o755)
            destination = folder / "installed"
            log = folder / "setup.log"
            env = {**os.environ, "MOCK_BIN": tools.as_posix(), "MOCK_SETUP": setup.as_posix(),
                   "MOCK_SETUP_LOG": log.as_posix(), "MOCK_DEST": destination.as_posix(),
                   "MOCK_INSTALL": (PROJECT / "install.sh").as_posix()}
            command = 'export PATH="$MOCK_BIN:$PATH"; bash "$MOCK_INSTALL" --repo https://github.com/example/test.git --dir "$MOCK_DEST" --domain panel.example.com'
            if os.name == "nt":
                command = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; bash "$MOCK_INSTALL" --repo https://github.com/example/test.git --dir "$(cygpath -u "$MOCK_DEST")" --domain panel.example.com'
            first = subprocess.run([BASH, "-c", command], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(log.read_text().strip(), "--domain panel.example.com")
            again = subprocess.run([BASH, "-c", command], env=env, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)

    def test_bash_syntax(self):
        for file in [PROJECT / "install.sh", *list((PROJECT / "deploy").glob("*.sh"))]:
            subprocess.run([BASH, "-n", file.as_posix()], check=True, capture_output=True)

    def test_setup_preserves_keys_adds_https_and_propagates_failure(self):
        with tempfile.TemporaryDirectory(prefix="funpay-installer-") as directory:
            folder = Path(directory)
            (folder / "deploy").mkdir()
            shutil.copy(PROJECT / "deploy/setup.sh", folder / "deploy/setup.sh")
            shutil.copy(PROJECT / "deploy/check-project.sh", folder / "deploy/check-project.sh")
            tools = folder / "bin"
            tools.mkdir()
            commands = {
                "id": "echo 0\n",
                "systemctl": "exit 0\n",
                "openssl": "printf '%064d\\n' 1\n",
                "docker": 'printf "%s\\n" "$*" >> "$MOCK_DOCKER_LOG"\nif [[ ${MOCK_FAIL_UP:-0} == 1 && "$*" == *"up -d"* ]]; then exit 55; fi\n',
            }
            for name, body in commands.items():
                file = tools / name
                file.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
                file.chmod(0o755)
            log = folder / "docker.log"
            # MSYS rewrites Windows PATH on entry; set the fake directory inside Bash.
            command = 'export PATH="$MOCK_BIN:$PATH"; bash deploy/setup.sh "$@"'
            if os.name == "nt":
                command = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; bash deploy/setup.sh "$@"'
            env = {**os.environ, "MOCK_BIN": tools.as_posix(), "MOCK_DOCKER_LOG": log.as_posix()}
            def run(*args, failure=False):
                return subprocess.run([BASH, "-c", command, "setup", *args], cwd=folder, env={**env, "MOCK_FAIL_UP": "1" if failure else "0"}, capture_output=True, text=True)
            first = run("--prepare-only")
            self.assertEqual(first.returncode, 0, first.stderr)
            original = dotenv_values(folder / ".env")
            second = run("--domain", "panel.example.com")
            self.assertEqual(second.returncode, 0, second.stderr)
            current = dotenv_values(folder / ".env")
            self.assertEqual(current["SECRET_KEY"], original["SECRET_KEY"])
            self.assertEqual(current["API_TOKEN"], original["API_TOKEN"])
            self.assertEqual(current["PUBLIC_ORIGIN"], "https://panel.example.com")
            self.assertIn("-f deploy/compose.https.yml", log.read_text())
            self.assertNotEqual(run(failure=True).returncode, 0)
            self.assertNotEqual(run("--domain", "https://bad.example").returncode, 0)
