import pytest
from cryptography.fernet import Fernet

from app.utils.crypto import CredentialEncryptionError, decrypt, encrypt


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt("hunter2")
    assert ciphertext != "hunter2"
    assert decrypt(ciphertext) == "hunter2"


def test_encrypt_returns_none_for_blank_plaintext(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert encrypt("") is None
    assert encrypt(None) is None


def test_decrypt_returns_none_for_blank_token(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert decrypt("") is None
    assert decrypt(None) is None


def test_missing_key_raises_credential_encryption_error(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(CredentialEncryptionError, match="not set"):
        encrypt("hunter2")


def test_placeholder_key_raises_credential_encryption_error(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "<GENERATE_A_FERNET_KEY>")
    with pytest.raises(CredentialEncryptionError, match="isn't a valid Fernet key"):
        encrypt("hunter2")


def test_decrypt_with_key_that_never_encrypted_it_raises_credential_encryption_error(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt("hunter2")

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(CredentialEncryptionError, match="may have changed"):
        decrypt(ciphertext)
