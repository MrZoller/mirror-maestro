import os
import stat

import pytest


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

