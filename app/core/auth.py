"""
Authentication module supporting both legacy single-user mode and multi-user JWT mode.

When MULTI_USER_ENABLED=false (default):
  - Uses HTTP Basic Auth with AUTH_USERNAME/AUTH_PASSWORD from env
  - Backward compatible with existing deployments

When MULTI_USER_ENABLED=true:
  - Uses JWT tokens with database-backed users
  - Supports multiple users with admin/regular roles
  - Initial admin created from INITIAL_ADMIN_USERNAME/PASSWORD
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Union

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db


# Security schemes
http_basic = HTTPBasic(auto_error=False)
http_bearer = HTTPBearer(auto_error=False)


class TokenData(BaseModel):
    """Data extracted from JWT token."""
    username: str
    user_id: int
    is_admin: bool
    exp: datetime


class CurrentUser(BaseModel):
    """Represents the currently authenticated user."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: Optional[str] = None
    is_admin: bool = False


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        hashed_password.encode('utf-8')
    )


def get_password_hash(password: str) -> str:
    """Generate password hash."""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')


def create_access_token(user_id: int, username: str, is_admin: bool) -> str:
    """Create a JWT access token."""
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours)
    to_encode = {
        "sub": username,
        "user_id": user_id,
        "is_admin": is_admin,
        "exp": expire
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username: str = payload.get("sub")
        user_id: int = payload.get("user_id")
        is_admin: bool = payload.get("is_admin", False)
        exp_timestamp = payload.get("exp")

        if username is None or user_id is None or exp_timestamp is None:
            return None

        exp = datetime.fromtimestamp(exp_timestamp)
        return TokenData(username=username, user_id=user_id, is_admin=is_admin, exp=exp)
    except (JWTError, TypeError, ValueError, OSError):
        # JWTError: Invalid token
        # TypeError: exp_timestamp is not a number
        # ValueError: exp_timestamp is out of range for timestamp
        # OSError: exp_timestamp is out of range for platform
        return None


def _verify_legacy_credentials(credentials: HTTPBasicCredentials) -> str:
    """Verify credentials against legacy single-user config."""
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


async def _verify_jwt_token(
    token: str,
    db: AsyncSession
) -> CurrentUser:
    """Verify JWT token and return user."""
    from app.models import User

    token_data = decode_access_token(token)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    result = await db.execute(
        select(User).where(User.id == token_data.user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        id=user.id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin
    )


async def _verify_basic_credentials_multi_user(
    credentials: HTTPBasicCredentials,
    db: AsyncSession
) -> CurrentUser:
    """Verify Basic Auth credentials against database users."""
    from app.models import User

    result = await db.execute(
        select(User).where(User.username == credentials.username, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return CurrentUser(
        id=user.id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin
    )


async def verify_credentials(
    request: Request,
    basic_credentials: Optional[HTTPBasicCredentials] = Depends(http_basic),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    db: AsyncSession = Depends(get_db)
) -> Union[str, CurrentUser]:
    """
    Verify authentication credentials.

    Supports:
    - No auth (if auth_enabled=false AND multi_user_enabled=false)
    - Legacy single-user Basic Auth (if multi_user_enabled=false)
    - Multi-user JWT Bearer tokens
    - Multi-user Basic Auth (fallback for API clients)

    Returns:
    - str: username (legacy mode)
    - CurrentUser: full user object (multi-user mode)
    """
    # Multi-user mode always requires authentication (takes precedence over auth_enabled)
    if settings.multi_user_enabled:
        # Try Bearer token first
        if bearer_credentials is not None:
            return await _verify_jwt_token(bearer_credentials.credentials, db)

        # Fall back to Basic Auth (for API clients)
        if basic_credentials is not None:
            return await _verify_basic_credentials_multi_user(basic_credentials, db)

        # No credentials provided
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Auth disabled (only applies when multi_user_enabled=false)
    if not settings.auth_enabled:
        return "anonymous"

    # Legacy single-user mode
    if basic_credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    return _verify_legacy_credentials(basic_credentials)


async def require_admin(
    current_user: Union[str, CurrentUser] = Depends(verify_credentials)
) -> CurrentUser:
    """Require admin privileges for the current user."""
    # Legacy mode - assume admin (single user has all permissions)
    if isinstance(current_user, str):
        return CurrentUser(id=0, username=current_user, is_admin=True)

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )

    return current_user


async def get_current_user(
    current_user: Union[str, CurrentUser] = Depends(verify_credentials)
) -> CurrentUser:
    """Get the current authenticated user as a CurrentUser object."""
    if isinstance(current_user, str):
        # Legacy mode - create a pseudo-user
        return CurrentUser(id=0, username=current_user, is_admin=True)
    return current_user
