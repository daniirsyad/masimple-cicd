import os

from cryptography.fernet import Fernet, InvalidToken


def _get_cipher():
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not set — required to encrypt/decrypt stored "
            "credentials (AI provider keys, Git/registry tokens). Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext):
    """Encrypt a credential for storage. Returns None unchanged (nothing to store)."""
    if not plaintext:
        return None
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt(token):
    """Decrypt a stored credential. Returns None unchanged (nothing was stored)."""
    if not token:
        return None
    try:
        return _get_cipher().decrypt(token.encode() if isinstance(token, str) else token).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Stored credential could not be decrypted — CREDENTIAL_ENCRYPTION_KEY may have changed."
        ) from exc
