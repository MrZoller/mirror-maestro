import os
import secrets
from typing import Optional


class JWTSecretManager:
    """Handle generation and persistence of JWT secret key."""

    def __init__(self):
        # Lazily initialized to avoid filesystem writes at import time.
        self._secret: Optional[str] = None

    def _get_or_create_secret(self) -> str:
        """Get or create a JWT secret key."""
        # Backwards-compatible defaults:
        # - If JWT_SECRET_KEY is set, use it directly (recommended for containers).
        # - Otherwise, if JWT_SECRET_KEY_PATH is set, read/write the key there.
        # - Otherwise use ./data/jwt_secret.key relative to the working directory.
        env_secret = os.getenv("JWT_SECRET_KEY")
        if env_secret:
            return env_secret

        key_file = os.getenv("JWT_SECRET_KEY_PATH") or "./data/jwt_secret.key"

        # Create data directory if it doesn't exist
        os.makedirs(os.path.dirname(key_file) or ".", exist_ok=True)

        if os.path.exists(key_file):
            with open(key_file, "r") as f:
                secret = f.read().strip()
            # Best-effort permission hardening for existing keys.
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            return secret
        else:
            # Generate a new secret key
            secret = secrets.token_urlsafe(32)
            with open(key_file, "w") as f:
                f.write(secret)
            # Set restrictive permissions
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            return secret

    def get_secret(self) -> str:
        """Get the JWT secret key, generating and persisting it if needed."""
        if self._secret is None:
            self._secret = self._get_or_create_secret()
        return self._secret


jwt_secret_manager = JWTSecretManager()
