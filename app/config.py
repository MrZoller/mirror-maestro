import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000

    # Database Configuration (PostgreSQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/mirror_maestro"

    # Authentication (legacy single-user mode, still supported for backward compatibility)
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Multi-user authentication (JWT)
    # If True, use database users instead of single auth_username/auth_password
    multi_user_enabled: bool = False
    jwt_secret_key: str = secrets.token_urlsafe(32)  # Auto-generate if not set
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Initial admin user (created on first startup if multi_user_enabled)
    initial_admin_username: str = "admin"
    initial_admin_password: str = "changeme"
    initial_admin_email: str = ""

    # Logging
    log_level: str = "INFO"

    # Application Settings
    app_title: str = "Mirror Maestro"
    app_description: str = "Orchestrate GitLab mirrors across multiple instance pairs with precision"

    # SSL/TLS Configuration
    ssl_enabled: bool = False
    ssl_cert_path: str = "/etc/nginx/ssl/cert.pem"
    ssl_key_path: str = "/etc/nginx/ssl/key.pem"

    # Rate Limiting (for batch operations and imports)
    # Delay between GitLab API operations to avoid overwhelming instances
    gitlab_api_delay_ms: int = 200  # Delay in milliseconds (200ms = ~300 ops/min, well under 600/min limit)
    gitlab_api_max_retries: int = 3  # Number of retries on rate limit errors


settings = Settings()
