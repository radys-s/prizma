import base64
import os
import pytest
from app import crypto
from app.config import settings


def test_encrypt_decrypt_roundtrip(monkeypatch):
    key_bytes = os.urandom(32)
    monkeypatch.setattr(settings, "secret_key", key_bytes.hex())
    
    plaintext = "sk-test-1234567890secretkey"
    nonce, ciphertext = crypto.encrypt(plaintext)
    decrypted = crypto.decrypt(nonce, ciphertext)
    assert decrypted == plaintext


def test_base64_and_hex_keys(monkeypatch):
    key_bytes = os.urandom(32)
    
    # Hex key (64 chars)
    hex_key = key_bytes.hex()
    monkeypatch.setattr(settings, "secret_key", hex_key)
    assert crypto._master_key() == key_bytes
    
    # Base64 key (44 chars)
    b64_key = base64.b64encode(key_bytes).decode("ascii")
    monkeypatch.setattr(settings, "secret_key", b64_key)
    assert crypto._master_key() == key_bytes


def test_wrong_key_raises_crypto_error(monkeypatch):
    key1 = os.urandom(32).hex()
    key2 = os.urandom(32).hex()
    
    monkeypatch.setattr(settings, "secret_key", key1)
    nonce, ciphertext = crypto.encrypt("secret_value")
    
    monkeypatch.setattr(settings, "secret_key", key2)
    with pytest.raises(crypto.CryptoError, match="nejde dešifrovat"):
        crypto.decrypt(nonce, ciphertext)


def test_invalid_key_format_raises_crypto_error(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "shortkey")
    with pytest.raises(crypto.CryptoError, match="SECRET_KEY musí být 32 bajtů"):
        crypto._master_key()
