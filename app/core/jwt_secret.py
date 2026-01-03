import os
import secrets
from typing import Optional


class JWTSecretManager:
    """Handle generation and persistence of JWT secret key."""

    def __init__(self):
        # Lazily initialized to avoid filesystem writes at import time.
        self._secret: Optional[str] = None

    def _get_or_create_secret(self, env_secret: Optional[str] = None, env_path: Optional[str] = None) -> str:
        """Get or create a JWT secret key.

        Args:
            env_secret: JWT_SECRET_KEY value from settings (takes precedence over env vars)
            env_path: JWT_SECRET_KEY_PATH value from settings (takes precedence over env vars)
        """
        # Priority order:
        # 1. Provided env_secret (from Pydantic settings)
        # 2. OS environment variable (for actual env vars, not .env file)
        # 3. Provided env_path or default path

        # Check provided secret first (from Pydantic settings)
        if env_secret:
            return env_secret

        # Fall back to actual OS environment variables (not .env file)
        os_secret = os.getenv("JWT_SECRET_KEY")
        if os_secret:
            return os_secret

        # Determine key file path
        key_file = env_path or os.getenv("JWT_SECRET_KEY_PATH") or "./data/jwt_secret.key"

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

    def get_secret(self, env_secret: Optional[str] = None, env_path: Optional[str] = None) -> str:
        """Get the JWT secret key, generating and persisting it if needed.

        Args:
            env_secret: JWT_SECRET_KEY value from settings
            env_path: JWT_SECRET_KEY_PATH value from settings
        """
        if self._secret is None:
            self._secret = self._get_or_create_secret(env_secret, env_path)
        return self._secret


jwt_secret_manager = JWTSecretManager()
