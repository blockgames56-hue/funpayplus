import base64
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.crypto import Vault
from app.promotions import discount_price
from app.schemas import (
    AccountCreate,
    AppSettings,
    MessageCreate,
    PluginSettings,
    RuleCreate,
    TelegramSettings,
)
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.vault = Vault("a" * 64)

    def test_roundtrip_unicode(self):
        self.assertEqual(self.vault.decrypt(self.vault.encrypt("sample✓")), "sample✓")

    def test_nonce_is_random(self):
        self.assertNotEqual(self.vault.encrypt("sample"), self.vault.encrypt("sample"))

    def test_wrong_key_rejected(self):
        with self.assertRaises(InvalidTag):
            Vault("b" * 64).decrypt(self.vault.encrypt("sample"))

    def test_tampering_rejected(self):
        blob = bytearray(base64.urlsafe_b64decode(self.vault.encrypt("sample")))
        blob[-1] ^= 1
        with self.assertRaises(InvalidTag):
            self.vault.decrypt(base64.urlsafe_b64encode(blob).decode())

    def test_secret_not_in_ciphertext(self):
        self.assertNotIn("sample-golden-key", self.vault.encrypt("sample-golden-key"))


class ValidationTests(unittest.TestCase):
    def test_promotion_price_rounding(self):
        self.assertEqual(discount_price(Decimal("99.99"), 10), Decimal("89.99"))

    def test_account_uses_profile_name(self):
        self.assertFalse(hasattr(AccountCreate(golden_key="sample" * 4), "title"))

    def test_reply_commands_and_rating_bounds(self):
        settings = PluginSettings(
            commands=[{"trigger": "/sample", "reply": "Есть!"}], review_replies={1: "Исправим"}
        )
        self.assertEqual(settings.commands[0].match, "exact")
        with self.assertRaises(ValidationError):
            PluginSettings(review_replies={6: "Нельзя"})

    def rule(self, template):
        return RuleCreate(
            account_id=1,
            lot_title_pattern="Sample",
            delivery_type="static_text",
            message_template=template,
        )

    def test_template_variables(self):
        self.assertEqual(self.rule("{buyer}: {item} / {order_id} / {date}").account_id, 1)

    def test_template_attribute_access_rejected(self):
        with self.assertRaises(ValidationError):
            self.rule("{buyer.__class__}")

    def test_template_format_spec_rejected(self):
        with self.assertRaises(ValidationError):
            self.rule("{item:>999999999}")

    def test_template_conversion_rejected(self):
        with self.assertRaises(ValidationError):
            self.rule("{item!r}")

    def test_cookie_injection_rejected(self):
        with self.assertRaises(ValidationError):
            AccountCreate(title="Sample", golden_key="sample;other_cookie=value")

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValidationError):
            MessageCreate(text="Sample", is_my=True)

    def test_oversized_message_rejected(self):
        with self.assertRaises(ValidationError):
            MessageCreate(text="x" * 20001)

    def test_invalid_telegram_token_rejected(self):
        with self.assertRaises(ValidationError):
            TelegramSettings(token="https://evil.example/token")

    def test_invalid_timezone_rejected(self):
        with self.assertRaises(ValidationError):
            AppSettings(timezone="Not/A/Timezone")


class AllowedOriginsTests(unittest.TestCase):
    def test_tauri_origins_are_always_allowed(self):
        from app import security

        with patch.object(security.config, "allowed_origins", "https://panel.example.com"):
            origins = security.allowed_origins()
        self.assertIn("https://panel.example.com", origins)
        self.assertIn("tauri://localhost", origins)
        self.assertIn("http://tauri.localhost", origins)

    def test_blank_entries_and_duplicates_removed(self):
        from app import security

        with patch.object(
            security.config, "allowed_origins", "https://a.test, https://a.test ,,https://b.test"
        ), patch.object(security.config, "public_origin", "https://a.test"):
            origins = security.allowed_origins()
        self.assertEqual(
            origins,
            ["https://a.test", "https://b.test", "tauri://localhost",
             "http://tauri.localhost"],
        )


if __name__ == "__main__":
    unittest.main()
