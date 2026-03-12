import base64
import functools
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


@functools.lru_cache(maxsize=1)
def _get_fernet():
    """Derive a Fernet key from Django's SECRET_KEY via PBKDF2 (cached)."""
    key_material = hashlib.pbkdf2_hmac(
        "sha256",
        settings.SECRET_KEY.encode(),
        b"EncryptedCharField",
        100_000,
    )
    return Fernet(base64.urlsafe_b64encode(key_material))


class EncryptedCharField(models.CharField):
    """CharField that transparently encrypts/decrypts values using Fernet.

    Stored as base64 ciphertext in the database; returns plaintext in Python.
    """

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return _get_fernet().encrypt(value.encode()).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Value is not encrypted (legacy plaintext) — return as-is
            return value
