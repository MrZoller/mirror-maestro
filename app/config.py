import logging
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Environment mode (development, staging, production)
    # In production mode, stricter validation is enforced
    environment: str = "development"

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000

    # Database Configuration (PostgreSQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/mirror_maestro"

    # Database Connection Pool Settings
    db_pool_size: int = 20  # Number of persistent connections
    db_pool_max_overflow: int = 40  # Additional connections under load
    db_pool_recycle: int = 3600  # Recycle connections after 1 hour (seconds)
    db_pool_pre_ping: bool = True  # Test connections before use

    # Authentication (legacy single-user mode, still supported for backward compatibility)
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Multi-user authentication (JWT)
    # If True, use database users instead of single auth_username/auth_password
    multi_user_enabled: bool = False
    # JWT secret key is managed by jwt_secret_manager - it's auto-generated and persisted
    # Can be overridden by setting JWT_SECRET_KEY or JWT_SECRET_KEY_PATH environment variables
    jwt_secret_key_env: Optional[str] = Field(default=None, validation_alias="JWT_SECRET_KEY")
    jwt_secret_key_path_env: Optional[str] = Field(default=None, validation_alias="JWT_SECRET_KEY_PATH")
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    @property
    def jwt_secret_key(self) -> str:
        """Get the JWT secret key from the secret manager.

        Passes Pydantic-loaded values from .env to the manager so it can honor
        JWT_SECRET_KEY and JWT_SECRET_KEY_PATH set in .env files.
        """
        from app.core.jwt_secret import jwt_secret_manager
        return jwt_secret_manager.get_secret(
            env_secret=self.jwt_secret_key_env,
            env_path=self.jwt_secret_key_path_env
        )

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
    gitlab_api_timeout: int = 60  # Timeout for GitLab API requests in seconds

    # Issue Sync Configuration
    # Circuit breaker settings for GitLab API resilience
    circuit_breaker_failure_threshold: int = 5  # Number of failures before opening circuit
    circuit_breaker_recovery_timeout: int = 60  # Seconds to wait before attempting recovery

    # Pagination limits to prevent memory exhaustion
    max_issues_per_sync: int = 10000  # Maximum issues to sync in one operation (100 pages * 100 per page)
    max_pages_per_request: int = 100  # Maximum pagination pages for API requests

    # Attachment handling
    max_attachment_size_mb: int = 100  # Maximum attachment size in MB (0 = unlimited)
    attachment_download_timeout: int = 30  # Timeout for downloading attachments in seconds

    # Batch processing
    issue_batch_size: int = 50  # Number of issues to process before committing progress checkpoint

    # Graceful shutdown
    sync_shutdown_timeout: int = 300  # Maximum seconds to wait for sync jobs to complete during shutdown

    # Stale job cleanup
    stale_job_timeout_minutes: int = 60  # Jobs running longer than this are considered stale and will be marked as failed

    @field_validator('environment')
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Validate environment is one of the allowed values."""
        allowed = {'development', 'staging', 'production'}
        if v.lower() not in allowed:
            raise ValueError(f"environment must be one of: {', '.join(allowed)}")
        return v.lower()

    @field_validator('db_pool_size')
    @classmethod
    def validate_db_pool_size(cls, v: int) -> int:
        """Ensure database pool size is positive."""
        if v <= 0:
            raise ValueError("db_pool_size must be positive")
        return v

    @field_validator('gitlab_api_delay_ms')
    @classmethod
    def validate_gitlab_api_delay(cls, v: int) -> int:
        """Ensure GitLab API delay is non-negative."""
        if v < 0:
            raise ValueError("gitlab_api_delay_ms cannot be negative")
        return v

    @field_validator('jwt_algorithm')
    @classmethod
    def validate_jwt_algorithm(cls, v: str) -> str:
        """Validate JWT algorithm is supported."""
        allowed = {'HS256', 'HS384', 'HS512'}
        if v not in allowed:
            raise ValueError(f"jwt_algorithm must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator('jwt_expiration_hours')
    @classmethod
    def validate_jwt_expiration(cls, v: int) -> int:
        """Ensure JWT expiration is within reasonable bounds."""
        if v <= 0:
            raise ValueError("jwt_expiration_hours must be positive")
        if v > 8760:  # 1 year
            raise ValueError("jwt_expiration_hours cannot exceed 8760 (1 year)")
        return v

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is a valid Python logging level."""
        allowed = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"log_level must be one of: {', '.join(sorted(allowed))}")
        return v_upper

    @field_validator('port')
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port is in valid range."""
        if v < 1 or v > 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @model_validator(mode='after')
    def validate_production_credentials(self) -> 'Settings':
        """
        In production mode, require that default credentials are changed.
        This prevents accidental deployment with insecure defaults.
        """
        if self.environment != 'production':
            return self

        errors = []

        # Check legacy auth password
        if self.auth_enabled and self.auth_password == 'changeme':
            errors.append(
                "AUTH_PASSWORD must be changed from default 'changeme' in production mode"
            )

        # Check multi-user admin password
        if self.multi_user_enabled and self.initial_admin_password == 'changeme':
            errors.append(
                "INITIAL_ADMIN_PASSWORD must be changed from default 'changeme' in production mode"
            )

        # Check database URL for default credentials
        if 'postgres:postgres@' in self.database_url:
            errors.append(
                "DATABASE_URL contains default credentials (postgres:postgres). "
                "Please use secure credentials in production mode"
            )

        if errors:
            error_msg = "Production mode security validation failed:\n  - " + "\n  - ".join(errors)
            raise ValueError(error_msg)

        return self


settings = Settings()
