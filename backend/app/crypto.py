"""Authenticated, version-bound encryption of stored credentials."""

import base64
import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Vault:
    def __init__(self, secret: str):
        self.cipher = AESGCM(hashlib.sha256(secret.encode()).digest())

    def encrypt(self, value: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = self.cipher.encrypt(nonce, value.encode(), b"funpay-plus:v1")
        return base64.urlsafe_b64encode(nonce + encrypted).decode()

    def decrypt(self, value: str) -> str:
        blob = base64.urlsafe_b64decode(value)
        return self.cipher.decrypt(blob[:12], blob[12:], b"funpay-plus:v1").decode()
