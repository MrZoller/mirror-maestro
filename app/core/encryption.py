import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class Encryption:
    """Handle encryption and decryption of sensitive data like tokens."""

    def __init__(self):
        # Lazily initialized to avoid filesystem writes at import time.
        self._key: Optional[bytes] = None
        self._cipher: Optional[Fernet] = None

    def _get_or_create_key(self) -> bytes:
        """Get or create an encryption key."""
        # Backwards-compatible defaults:
        # - If ENCRYPTION_KEY is set, use it directly (recommended for containers).
        # - Otherwise, if ENCRYPTION_KEY_PATH is set, read/write the key there.
        # - Otherwise use ./data/encryption.key relative to the working directory.
        env_key = os.getenv("ENCRYPTION_KEY")
        if env_key:
            # Fernet keys are already urlsafe-base64. Accept bytes or str.
            key = env_key.encode("utf-8")
            # Validate early with Fernet constructor.
            Fernet(key)
            return key

        key_file = os.getenv("ENCRYPTION_KEY_PATH") or "./data/encryption.key"

        # Create data directory if it doesn't exist
        os.makedirs(os.path.dirname(key_file) or ".", exist_ok=True)

        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key = f.read().strip()
            # Best-effort permission hardening for existing keys.
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            # Validate early so callers get a clear error.
            Fernet(key)
            return key
        else:
            # Generate a new key
            key = Fernet.generate_key()
            with open(key_file, "wb") as f:
                f.write(key)
            # Set restrictive permissions
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            return key

    def _get_cipher(self) -> Fernet:
        if self._cipher is None:
            self._key = self._get_or_create_key()
            self._cipher = Fernet(self._key)
        return self._cipher

    def encrypt(self, data: str) -> str:
        """Encrypt a string and return base64 encoded result."""
        encrypted = self._get_cipher().encrypt(data.encode())
        return base64.b64encode(encrypted).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt a base64 encoded string."""
        encrypted = base64.b64decode(encrypted_data.encode())
        try:
            decrypted = self._get_cipher().decrypt(encrypted)
            return decrypted.decode()
        except (InvalidToken, ValueError) as e:
            # ValueError can come from base64 decode / invalid key construction.
            raise ValueError("Failed to decrypt payload (invalid key or ciphertext)") from e


encryption = Encryption()
