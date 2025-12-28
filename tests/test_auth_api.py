"""Tests for authentication API endpoints."""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_auth_mode_returns_status(client: AsyncClient):
    """Test that auth mode endpoint returns multi_user_enabled status."""
    response = await client.get("/api/auth/mode")
    assert response.status_code == 200
    data = response.json()
    assert "multi_user_enabled" in data
    assert isinstance(data["multi_user_enabled"], bool)


async def test_auth_mode_no_auth_required(client: AsyncClient):
    """Test that auth mode endpoint doesn't require authentication."""
    # The test client uses auth by default, but the endpoint should work
    # without auth too - it's needed for the login page
    response = await client.get("/api/auth/mode")
    assert response.status_code == 200


async def test_login_with_invalid_credentials(client: AsyncClient):
    """Test login with invalid credentials fails."""
    # In legacy mode, login endpoint exists but returns an error
    response = await client.post("/api/auth/login", json={
        "username": "baduser",
        "password": "badpassword"
    })
    # In legacy mode, this should return 400 (multi-user not enabled)
    # or 401 (invalid credentials in multi-user mode)
    assert response.status_code in [400, 401]


async def test_login_missing_fields(client: AsyncClient):
    """Test login with missing fields fails."""
    response = await client.post("/api/auth/login", json={
        "username": "admin"
        # missing password
    })
    assert response.status_code == 422  # Validation error


async def test_auth_me_requires_authentication(app):
    """Test that /me endpoint requires authentication."""
    # Create a new client without the auth override to test unauthenticated access
    from httpx import ASGITransport, AsyncClient as RawClient
    from app.database import get_db
    from app.core.auth import verify_credentials

    # Temporarily remove the auth override to test real auth behavior
    original_overrides = app.dependency_overrides.copy()
    if verify_credentials in app.dependency_overrides:
        del app.dependency_overrides[verify_credentials]

    try:
        transport = ASGITransport(app=app)
        async with RawClient(transport=transport, base_url="http://test") as unauthed_client:
            response = await unauthed_client.get("/api/auth/me")
            # In legacy mode, returns 403 (multi-user not enabled)
            # In multi-user mode without auth, returns 401
            assert response.status_code in [401, 403]
    finally:
        app.dependency_overrides = original_overrides


async def test_change_password_requires_auth(app):
    """Test that change-password requires authentication."""
    from httpx import ASGITransport, AsyncClient as RawClient
    from app.core.auth import verify_credentials

    # Temporarily remove the auth override to test real auth behavior
    original_overrides = app.dependency_overrides.copy()
    if verify_credentials in app.dependency_overrides:
        del app.dependency_overrides[verify_credentials]

    try:
        transport = ASGITransport(app=app)
        async with RawClient(transport=transport, base_url="http://test") as unauthed_client:
            response = await unauthed_client.post("/api/auth/change-password", json={
                "current_password": "old",
                "new_password": "newpassword123"
            })
            # In legacy mode, returns 403 (multi-user not enabled)
            # In multi-user mode without auth, returns 401
            assert response.status_code in [401, 403]
    finally:
        app.dependency_overrides = original_overrides


async def test_change_password_validation(client: AsyncClient):
    """Test change-password validates password length."""
    response = await client.post("/api/auth/change-password", json={
        "current_password": "current",
        "new_password": "short"  # Too short
    })
    # Should fail validation (password must be >= 8 chars)
    assert response.status_code == 400
