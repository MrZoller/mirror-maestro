"""
API rate limiting for HTTP endpoints.

Provides protection against brute force attacks and API abuse.
Uses slowapi for rate limiting with Redis or in-memory storage.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse


def get_client_identifier(request: Request) -> str:
    """
    Get a unique identifier for the client making the request.

    Uses IP address by default, but can be extended to use
    authenticated user ID when available.
    """
    # Try to get user from request state (set by auth middleware)
    if hasattr(request.state, "user") and request.state.user:
        return f"user:{request.state.user}"

    # Fall back to IP address
    return get_remote_address(request)


# Create rate limiter instance
# Using in-memory storage by default (suitable for single-instance deployments)
# For multi-instance deployments, configure Redis storage
limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=["200/minute"],  # Default rate limit for all endpoints
    storage_uri="memory://",
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded errors."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please slow down your requests.",
            "retry_after": exc.detail
        },
        headers={"Retry-After": str(exc.detail)}
    )


# Rate limit decorators for different endpoint types
# Usage: @limiter.limit("5/minute")

# Auth endpoints - strict limits to prevent brute force
AUTH_RATE_LIMIT = "5/minute"

# Write operations - moderate limits
WRITE_RATE_LIMIT = "30/minute"

# Read operations - generous limits
READ_RATE_LIMIT = "100/minute"

# Sync operations - very strict to prevent abuse
SYNC_RATE_LIMIT = "10/minute"
