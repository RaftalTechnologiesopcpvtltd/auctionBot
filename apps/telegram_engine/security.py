"""Telegram Engine security and token cryptographic utilities."""
import base64
import hashlib
import logging
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """Derive a deterministic 32-byte urlsafe base64 key from settings.SECRET_KEY or dedicated key."""
    raw_key = getattr(settings, "TELEGRAM_TOKEN_ENCRYPTION_KEY", None) or settings.SECRET_KEY
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_token(plain_token: str) -> str:
    """Encrypt a plaintext bot token at rest using standard Fernet symmetric encryption."""
    if not plain_token:
        return ""
    f = Fernet(get_encryption_key())
    return f.encrypt(plain_token.strip().encode("utf-8")).decode("utf-8")


def decrypt_token(cipher_token: str) -> str:
    """Decrypt an encrypted bot token from storage."""
    if not cipher_token:
        return ""
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(cipher_token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt Telegram bot token: invalid ciphertext or modified key.")
        return ""


def mask_token(token: str) -> str:
    """Return a safe masked representation of a token for display in admin or logs."""
    if not token or len(token) < 10:
        return "********"
    return f"{token[:4]}...{token[-4:]}"
