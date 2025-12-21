import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2


class Encryption:
    """Handle encryption and decryption of sensitive data like tokens."""

    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)

    def _get_or_create_key(self) -> bytes:
        """Get or create an encryption key."""
        key_file = "./data/encryption.key"

        # Create data directory if it doesn't exist
        os.makedirs("./data", exist_ok=True)

        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                return f.read()
        else:
            # Generate a new key
            key = Fernet.generate_key()
            with open(key_file, "wb") as f:
                f.write(key)
            # Set restrictive permissions
            os.chmod(key_file, 0o600)
            return key

    def encrypt(self, data: str) -> str:
        """Encrypt a string and return base64 encoded result."""
        encrypted = self.cipher.encrypt(data.encode())
        return base64.b64encode(encrypted).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt a base64 encoded string."""
        encrypted = base64.b64decode(encrypted_data.encode())
        decrypted = self.cipher.decrypt(encrypted)
        return decrypted.decode()


encryption = Encryption()
