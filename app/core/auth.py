import secrets
from typing import Optional
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.config import settings


security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Security(security)) -> str:
    """Verify HTTP Basic authentication credentials."""
    if not settings.auth_enabled:
        return "authenticated"

    correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"),
        settings.auth_username.encode("utf8")
    )
    correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"),
        settings.auth_password.encode("utf8")
    )

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username
