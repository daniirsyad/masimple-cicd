import os

from cryptography.fernet import Fernet, InvalidToken

GENERATE_KEY_HINT = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class CredentialEncryptionError(RuntimeError):
    """CREDENTIAL_ENCRYPTION_KEY is missing, malformed, or no longer matches
    what a stored credential was encrypted with — always a deployment/config
    problem, never a bug in the request that triggered it. Its own subclass
    (rather than a bare RuntimeError) so app/__init__.py's error handler can
    turn it into a clean flash instead of a raw 500, without also catching
    unrelated RuntimeErrors elsewhere in the app.
    """


def _get_cipher():
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set — required to encrypt/decrypt stored "
            f"credentials (AI provider keys, Git/registry tokens). Generate one with: {GENERATE_KEY_HINT}"
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is set but isn't a valid Fernet key (must be 32 url-safe "
            f"base64-encoded bytes) — check for a leftover placeholder value. Generate a real one with: "
            f"{GENERATE_KEY_HINT}"
        ) from exc


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
        raise CredentialEncryptionError(
            "Stored credential could not be decrypted — CREDENTIAL_ENCRYPTION_KEY may have changed."
        ) from exc
