"""
Authentication API endpoints.

Provides login, logout, and current user info endpoints.
"""
import logging
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
    CurrentUser,
)
from app.core.api_rate_limiter import limiter, AUTH_RATE_LIMIT
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Login request body."""
    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response with JWT token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: "UserResponse"


class UserResponse(BaseModel):
    """User information response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: Optional[str] = None
    is_admin: bool
    is_active: bool
    created_at: datetime


class AuthModeResponse(BaseModel):
    """Response indicating the authentication mode."""
    auth_enabled: bool
    multi_user_enabled: bool
    # For legacy mode, client should use Basic Auth
    # For multi-user mode, client should use the login endpoint


@router.get("/mode", response_model=AuthModeResponse)
async def get_auth_mode():
    """
    Get the current authentication mode.

    This endpoint is public and helps the frontend determine
    how to authenticate (Basic Auth vs JWT login form).
    """
    return AuthModeResponse(
        auth_enabled=settings.auth_enabled,
        multi_user_enabled=settings.multi_user_enabled
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user and return JWT token.

    This endpoint is only used in multi-user mode.
    In legacy mode, use HTTP Basic Auth instead.
    """
    try:
        return await _perform_login(login_data, db)
    except HTTPException:
        # Re-raise HTTP exceptions as-is (these are expected errors)
        raise
    except Exception as e:
        # Log unexpected errors and return a generic error message
        # This ensures we always return JSON, even for unexpected failures
        logger.error(f"Unexpected error during login: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login. Please check the server logs."
        )


async def _perform_login(login_data: LoginRequest, db: AsyncSession) -> LoginResponse:
    """Internal login logic, separated for cleaner exception handling."""
    if not settings.multi_user_enabled:
        # In legacy mode, check against env credentials using constant-time comparison
        # to prevent timing attacks
        correct_username = secrets.compare_digest(
            login_data.username.encode("utf-8"),
            settings.auth_username.encode("utf-8")
        )
        correct_password = secrets.compare_digest(
            login_data.password.encode("utf-8"),
            settings.auth_password.encode("utf-8")
        )
        if correct_username and correct_password:
            # Create a pseudo-token for legacy mode compatibility
            token = create_access_token(user_id=0, username=login_data.username, is_admin=True)
            return LoginResponse(
                access_token=token,
                token_type="bearer",
                expires_in=settings.jwt_expiration_hours * 3600,
                user=UserResponse(
                    id=0,
                    username=login_data.username,
                    email=None,
                    is_admin=True,
                    is_active=True,
                    created_at=datetime.utcnow()
                )
            )

        # Log authentication failure for security monitoring
        from app.core.logging_utils import sanitize_for_logging
        logger.warning(
            f"Login failed for user '{sanitize_for_logging(login_data.username, max_length=50)}' "
            f"(legacy mode)"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    # Multi-user mode - check database
    result = await db.execute(
        select(User).where(User.username == login_data.username)
    )
    user = result.scalar_one_or_none()

    # Always perform password verification to prevent timing-based user enumeration.
    # If user doesn't exist, verify against a dummy hash to maintain constant time.
    # This prevents attackers from determining valid usernames by timing differences.
    # Dummy hash is a pre-computed bcrypt hash that takes similar time to verify.
    DUMMY_HASH = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.E/fN.xwU3jG3Iy"
    password_to_verify = user.hashed_password if user else DUMMY_HASH
    password_valid = verify_password(login_data.password, password_to_verify)

    if user is None or not password_valid:
        # Log authentication failure for security monitoring
        from app.core.logging_utils import sanitize_for_logging
        logger.warning(
            f"Login failed for user '{sanitize_for_logging(login_data.username, max_length=50)}' "
            f"(multi-user mode)"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    if not user.is_active:
        # Log authentication failure for security monitoring
        logger.warning(
            f"Login failed - account disabled "
            f"(user_id: {user.id}, username: {user.username})"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled"
        )

    # Create JWT token
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expiration_hours * 3600,
        user=UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_admin=user.is_admin,
            is_active=user.is_active,
            created_at=user.created_at
        )
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get the current authenticated user's information.
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_active=True,
        created_at=datetime.utcnow()  # Legacy mode doesn't have real timestamps
    )


class ChangePasswordRequest(BaseModel):
    """Request to change password."""
    current_password: str
    new_password: str


@router.post("/change-password")
@limiter.limit(AUTH_RATE_LIMIT)
async def change_password(
    request: Request,
    password_data: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Change the current user's password.

    Only available in multi-user mode.
    """
    if not settings.multi_user_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password change not available in single-user mode. Update AUTH_PASSWORD in environment."
        )

    # Get user from database
    result = await db.execute(
        select(User).where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Verify current password
    if not verify_password(password_data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Update password
    user.hashed_password = get_password_hash(password_data.new_password)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to commit password change: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update password. Please try again."
        )

    logger.info(f"Password changed successfully for user '{user.username}'")
    return {"message": "Password changed successfully"}
