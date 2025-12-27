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

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:///./data/mirrors.db"

    # Authentication
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Logging
    log_level: str = "INFO"

    # Application Settings
    app_title: str = "Mirror Maestro"
    app_description: str = "Orchestrate GitLab mirrors across multiple instance pairs with precision"


settings = Settings()
