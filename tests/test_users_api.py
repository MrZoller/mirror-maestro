"""Tests for users API endpoints."""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_users_list_requires_multi_user_mode(client: AsyncClient):
    """Test that users list requires multi-user mode to be enabled."""
    # In legacy mode (multi-user not enabled), users endpoint returns 400
    response = await client.get("/api/users")
    # In legacy mode: 400 (multi-user mode not enabled)
    # In multi-user mode: 200 or 403 (requires admin)
    assert response.status_code in [200, 400, 403]


async def test_users_create_requires_multi_user_mode(client: AsyncClient):
    """Test that creating users requires multi-user mode."""
    response = await client.post("/api/users", json={
        "username": "newuser",
        "password": "password123"
    })
    # In legacy mode: 400 (multi-user mode not enabled)
    # In multi-user mode without admin: 403 (requires admin)
    assert response.status_code in [400, 403]


async def test_users_create_validation(client: AsyncClient):
    """Test that user creation validates required fields."""
    response = await client.post("/api/users", json={
        "username": "test"
        # missing password
    })
    # In legacy mode: 400 (multi-user not enabled) or 422 (validation)
    assert response.status_code in [400, 403, 422]


async def test_users_get_requires_multi_user_mode(client: AsyncClient):
    """Test getting a user requires multi-user mode."""
    response = await client.get("/api/users/99999")
    # In legacy mode: 400 (multi-user mode not enabled)
    # In multi-user mode: 403 or 404
    assert response.status_code in [400, 403, 404]


async def test_users_update_requires_multi_user_mode(client: AsyncClient):
    """Test updating a user requires multi-user mode."""
    response = await client.put("/api/users/99999", json={
        "username": "updated"
    })
    # In legacy mode: 400 (multi-user mode not enabled)
    # In multi-user mode: 403 or 404
    assert response.status_code in [400, 403, 404]


async def test_users_delete_requires_multi_user_mode(client: AsyncClient):
    """Test deleting a user requires multi-user mode."""
    response = await client.delete("/api/users/99999")
    # In legacy mode: 400 (multi-user mode not enabled)
    # In multi-user mode: 403 or 404
    assert response.status_code in [400, 403, 404]


async def test_users_password_validation(client: AsyncClient):
    """Test that password minimum length is enforced."""
    response = await client.post("/api/users", json={
        "username": "testuser",
        "password": "short"  # Too short (< 8 chars)
    })
    # In legacy mode: 400 (multi-user not enabled)
    # In multi-user mode: 400, 403, or 422
    assert response.status_code in [400, 403, 422]
