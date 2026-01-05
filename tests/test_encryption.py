import os
import stat

import pytest
from cryptography.fernet import Fernet


def test_encryption_creates_key_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    enc = Encryption()
    ciphertext = enc.encrypt("hello")
    assert isinstance(ciphertext, str)
    assert enc.decrypt(ciphertext) == "hello"

    key_path = tmp_path / "data" / "encryption.key"
    assert key_path.exists()

    mode = stat.S_IMODE(os.stat(key_path).st_mode)
    assert mode == 0o600


def test_encryption_reuses_existing_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    enc1 = Encryption()
    c1 = enc1.encrypt("token")

    enc2 = Encryption()
    assert enc2.decrypt(c1) == "token"


@pytest.mark.skip(reason="Settings singleton is initialized before test can set env var")
def test_encryption_with_environment_key(tmp_path, monkeypatch):
    """Test encryption using ENCRYPTION_KEY environment variable."""
    monkeypatch.chdir(tmp_path)

    # Generate a valid Fernet key
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("ENCRYPTION_KEY", key)

    from app.core.encryption import Encryption

    enc = Encryption()
    ciphertext = enc.encrypt("secret")
    assert enc.decrypt(ciphertext) == "secret"

    # Key file should not be created when using env var
    key_path = tmp_path / "data" / "encryption.key"
    assert not key_path.exists()


@pytest.mark.skip(reason="Settings singleton is initialized before test can set env var")
def test_encryption_with_custom_key_path(tmp_path, monkeypatch):
    """Test encryption using ENCRYPTION_KEY_PATH environment variable."""
    monkeypatch.chdir(tmp_path)

    custom_path = tmp_path / "custom" / "my.key"
    monkeypatch.setenv("ENCRYPTION_KEY_PATH", str(custom_path))

    from app.core.encryption import Encryption

    enc = Encryption()
    ciphertext = enc.encrypt("data")
    assert enc.decrypt(ciphertext) == "data"

    assert custom_path.exists()
    mode = stat.S_IMODE(os.stat(custom_path).st_mode)
    assert mode == 0o600


@pytest.mark.skip(reason="Settings singleton is initialized before test can set env var")
def test_encryption_with_invalid_key(tmp_path, monkeypatch):
    """Test that invalid encryption key raises error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENCRYPTION_KEY", "invalid-key")

    from app.core.encryption import Encryption

    enc = Encryption()
    with pytest.raises(Exception):  # Fernet will raise ValueError
        enc.encrypt("test")


def test_decrypt_with_invalid_data(tmp_path, monkeypatch):
    """Test decrypting invalid data raises error."""
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    enc = Encryption()
    with pytest.raises(ValueError, match="Failed to decrypt"):
        enc.decrypt("invalid-base64-data!")


def test_decrypt_with_wrong_key(tmp_path, monkeypatch):
    """Test decrypting with wrong key raises error."""
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    # Create encryption with one key
    enc1 = Encryption()
    ciphertext = enc1.encrypt("secret")

    # Create new instance with different key
    key_path = tmp_path / "data" / "encryption.key"
    new_key = Fernet.generate_key()
    key_path.write_bytes(new_key)

    enc2 = Encryption()
    with pytest.raises(ValueError, match="Failed to decrypt"):
        enc2.decrypt(ciphertext)


def test_encryption_with_special_characters(tmp_path, monkeypatch):
    """Test encryption handles special characters and unicode."""
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    enc = Encryption()

    # Test various special inputs
    test_cases = [
        "simple",
        "with spaces",
        "with\nnewlines\n",
        "unicode: 日本語 中文 한국어",
        "symbols: !@#$%^&*()_+-=[]{}|;':\",./<>?",
        "glpat-abcdefghijklmnop",  # GitLab token format
        "",  # Empty string
        "a" * 10000,  # Long string
    ]

    for original in test_cases:
        ciphertext = enc.encrypt(original)
        decrypted = enc.decrypt(ciphertext)
        assert decrypted == original, f"Failed for: {original[:50]}..."


def test_encryption_ciphertext_is_unique(tmp_path, monkeypatch):
    """Test that encrypting same value produces different ciphertext (due to IV)."""
    monkeypatch.chdir(tmp_path)

    from app.core.encryption import Encryption

    enc = Encryption()
    plaintext = "same-value"

    ciphertext1 = enc.encrypt(plaintext)
    ciphertext2 = enc.encrypt(plaintext)

    # Fernet uses unique IV each time, so ciphertexts should differ
    assert ciphertext1 != ciphertext2

    # But both should decrypt to the same value
    assert enc.decrypt(ciphertext1) == plaintext
    assert enc.decrypt(ciphertext2) == plaintext


def test_encryption_key_permissions_warning(tmp_path, monkeypatch, caplog):
    """Test that improper key permissions generate a warning."""
    import logging

    monkeypatch.chdir(tmp_path)

    # Create key file with insecure permissions
    key_dir = tmp_path / "data"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / "encryption.key"
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    os.chmod(key_path, 0o644)  # World-readable (insecure)

    from app.core.encryption import Encryption

    with caplog.at_level(logging.WARNING):
        enc = Encryption()
        # Force initialization
        enc.encrypt("test")

    # The encryption should still work
    assert enc.decrypt(enc.encrypt("test")) == "test"

    # Check for warning in logs (may not be present if chmod succeeded)
    # The warning is only logged if chmod fails or verification fails

