import base64
import hashlib
from cryptography.fernet import Fernet
from app.config import settings


def _get_fernet() -> Fernet:
    """
    Derives a valid 32-byte base64 Fernet key from settings.SECRET_KEY via SHA-256.
    """
    key_hash = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    fernet_key = base64.urlsafe_b64encode(key_hash)
    return Fernet(fernet_key)


def encrypt_api_key(raw_key: str) -> str:
    """
    Encrypts a plaintext provider API key using AES-256 (Fernet envelope encryption).
    """
    if not raw_key:
        return ""
    fernet = _get_fernet()
    return fernet.encrypt(raw_key.encode('utf-8')).decode('utf-8')


def decrypt_api_key(encrypted_key: str) -> str:
    """
    Decrypts an AES-256 encrypted provider API key back to its plaintext representation.
    """
    if not encrypted_key:
        return ""
    fernet = _get_fernet()
    return fernet.decrypt(encrypted_key.encode('utf-8')).decode('utf-8')
