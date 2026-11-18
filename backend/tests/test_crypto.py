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


# ------------------------------------------------------ the local key file


@pytest.fixture()
def local_key(tmp_path, monkeypatch):
    """No SECRET_KEY, local mode: the key comes from a file in `tmp_path`."""
    path = tmp_path / "master.key"
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(settings, "public_mode", False)
    monkeypatch.setattr(crypto, "LOCAL_KEY_PATH", path)
    return path


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_generated_master_key_is_readable_by_its_owner_only(local_key):
    """It was written with the process umask, typically 0644 or 0664: any
    other account on the machine could read the key to every stored API key."""
    crypto._master_key()

    assert local_key.stat().st_mode & 0o777 == 0o600


def test_a_key_another_process_wrote_first_is_kept(local_key, monkeypatch):
    """Two starts racing to create the file must agree on one key, or rows
    encrypted under the loser's key can never be read again."""
    theirs = os.urandom(crypto.KEY_BYTES)
    real_open = os.open

    def other_process_wins(path, flags, mode=0o777):
        local_key.write_bytes(theirs)
        return real_open(path, flags, mode)

    monkeypatch.setattr(crypto.os, "open", other_process_wins)

    assert crypto._master_key() == theirs
    assert local_key.read_bytes() == theirs


def test_the_key_file_is_opened_in_binary_mode(local_key, monkeypatch):
    """`os.open` defaults to text mode on Windows, which writes every 0x0A
    byte of the key as CR LF: about one fresh install in eight would get a key
    file longer than 32 bytes and lose every stored key at the next start."""
    binary = 0x40000000  # stands in for os.O_BINARY, which only Windows has
    monkeypatch.setattr(os, "O_BINARY", binary, raising=False)
    seen: list[int] = []
    real_open = os.open

    def spy(path, flags, mode=0o777):
        seen.append(flags)
        return real_open(path, flags & ~binary, mode)

    monkeypatch.setattr(crypto.os, "open", spy)
    crypto._master_key()

    assert seen and seen[0] & binary


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_key_file_an_older_version_left_readable_is_tightened(local_key):
    key = os.urandom(crypto.KEY_BYTES)
    local_key.write_bytes(key)
    local_key.chmod(0o664)

    assert crypto._master_key() == key
    assert local_key.stat().st_mode & 0o777 == 0o600
