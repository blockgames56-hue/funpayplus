import os
import tempfile
import unittest
from pathlib import Path

from app.crypto import Vault
from app.os_secrets import dpapi, load_master_key


@unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
class DPAPITests(unittest.TestCase):
    def test_existing_ciphertext_survives_key_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            key = "offline-test-master-key-not-a-real-secret-123456"
            encrypted = Vault(key).encrypt("offline-token")
            (root / "encryption.key").write_text(key, encoding="utf-8")
            migrated = load_master_key(root)
            self.assertEqual(Vault(migrated).decrypt(encrypted), "offline-token")
            self.assertFalse((root / "encryption.key").exists())
            self.assertNotIn(key.encode(), (root / "encryption.key.dpapi").read_bytes())
            self.assertEqual(load_master_key(root), key)

    def test_corrupt_protected_key_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "encryption.key.dpapi").write_bytes(b"corrupt")
            with self.assertRaises(OSError):
                load_master_key(root)
            self.assertEqual((root / "encryption.key.dpapi").read_bytes(), b"corrupt")

    def test_real_windows_round_trip(self):
        protected = dpapi(b"synthetic-test-data")
        self.assertEqual(dpapi(protected, decrypt=True), b"synthetic-test-data")
