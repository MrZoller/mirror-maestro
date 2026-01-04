import base64
import logging
import os
import stat
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class Encryption:
    """Handle encryption and decryption of sensitive data like tokens."""

    def __init__(self):
        # Lazily initialized to avoid filesystem writes at import time.
        self._key: Optional[bytes] = None
        self._cipher: Optional[Fernet] = None
        self._lock = threading.Lock()

    def _get_or_create_key(self, env_key: Optional[str] = None, key_file: str = "./data/encryption.key") -> bytes:
        """Get or create an encryption key.

        Args:
            env_key: Optional encryption key from ENCRYPTION_KEY environment variable
            key_file: Path to encryption key file (from ENCRYPTION_KEY_PATH or default)
        """
        # Use provided env_key or fall back to configuration
        if env_key is None:
            from app.config import settings
            env_key = settings.encryption_key_env
            key_file = settings.encryption_key_path

        if env_key:
            # Fernet keys are already urlsafe-base64. Accept bytes or str.
            key = env_key.encode("utf-8")
            # Validate early with Fernet constructor.
            Fernet(key)
            return key

        # Create data directory if it doesn't exist
        os.makedirs(os.path.dirname(key_file) or ".", exist_ok=True)

        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key = f.read().strip()
            # Harden permissions for existing keys and verify
            self._secure_key_file(key_file)
            # Validate early so callers get a clear error.
            Fernet(key)
            return key
        else:
            # Generate a new key
            key = Fernet.generate_key()
            with open(key_file, "wb") as f:
                f.write(key)
            # Set restrictive permissions and verify
            self._secure_key_file(key_file)
            logger.info(f"Generated new encryption key at {key_file}")
            return key

    def _secure_key_file(self, key_file: str) -> None:
        """
        Secure the encryption key file with restrictive permissions.

        Logs warnings if permissions cannot be set or verified.
        """
        try:
            os.chmod(key_file, 0o600)
        except OSError as e:
            logger.warning(
                f"Could not set permissions on encryption key file {key_file}: {e}. "
                f"Ensure the file is only readable by the application user."
            )
            return

        # Verify permissions were set correctly
        try:
            current_mode = stat.S_IMODE(os.stat(key_file).st_mode)
            if current_mode != 0o600:
                logger.warning(
                    f"Encryption key file {key_file} has permissions {oct(current_mode)} "
                    f"instead of 0o600. This may expose the key to other users."
                )
        except OSError as e:
            logger.warning(f"Could not verify permissions on encryption key file: {e}")

    def _get_cipher(self) -> Fernet:
        # Double-checked locking pattern for thread-safe lazy initialization
        if self._cipher is None:
            with self._lock:
                # Re-check after acquiring lock (another thread may have initialized it)
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
        import binascii
        try:
            encrypted = base64.b64decode(encrypted_data.encode())
            decrypted = self._get_cipher().decrypt(encrypted)
            return decrypted.decode()
        except (InvalidToken, ValueError, binascii.Error) as e:
            # binascii.Error for invalid base64, InvalidToken for bad ciphertext,
            # ValueError from invalid key construction.
            raise ValueError("Failed to decrypt payload (invalid key or ciphertext)") from e


encryption = Encryption()
