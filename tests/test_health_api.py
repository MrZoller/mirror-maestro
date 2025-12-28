"""Tests for the health check API endpoint."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.encryption import encryption


@pytest.mark.asyncio
async def test_quick_health_returns_healthy(client):
    """Test quick health check returns healthy status."""
    response = await client.get("/api/health/quick")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_quick_health_no_auth_required(app):
    """Test quick health check does not require authentication."""
    from httpx import ASGITransport, AsyncClient

    # Create a client that doesn't use the auth override
    app.dependency_overrides.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/api/health/quick")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_detailed_health_empty_database(client):
    """Test detailed health check on empty database."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["version"] == "0.1.0"
    assert "timestamp" in data

    # Check components
    assert len(data["components"]) >= 2  # database and mirrors at minimum

    # Database should be healthy
    db_component = next(c for c in data["components"] if c["name"] == "database")
    assert db_component["status"] == "healthy"
    assert db_component["latency_ms"] is not None

    # Mirrors should be healthy (no mirrors = healthy)
    mirrors_component = next(c for c in data["components"] if c["name"] == "mirrors")
    assert mirrors_component["status"] == "healthy"
    assert "No mirrors configured" in mirrors_component["message"]

    # Check mirror summary
    assert data["mirrors"]["total"] == 0
    assert data["mirrors"]["health_percentage"] == 100.0

    # Check token summary
    assert data["tokens"]["total_with_tokens"] == 0


@pytest.mark.asyncio
async def test_detailed_health_with_healthy_mirrors(client, db_session, monkeypatch):
    """Test health check with all healthy mirrors."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create healthy mirrors
    for i in range(3):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=i,
            source_project_path=f"group/project-{i}",
            target_project_id=i + 100,
            target_project_path=f"group/project-{i}-mirror",
            enabled=True,
            last_update_status="success"
        )
        db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["mirrors"]["total"] == 3
    assert data["mirrors"]["enabled"] == 3
    assert data["mirrors"]["success"] == 3
    assert data["mirrors"]["failed"] == 0
    assert data["mirrors"]["health_percentage"] == 100.0


@pytest.mark.asyncio
async def test_detailed_health_with_failed_mirrors(client, db_session, monkeypatch):
    """Test health check with some failed mirrors."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mix of successful and failed mirrors
    for i in range(4):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=i,
            source_project_path=f"group/project-{i}",
            target_project_id=i + 100,
            target_project_path=f"group/project-{i}-mirror",
            enabled=True,
            last_update_status="success" if i < 3 else "failed"
        )
        db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    # Should be degraded (some failures but > 50% healthy)
    assert data["status"] == "degraded"
    assert data["mirrors"]["total"] == 4
    assert data["mirrors"]["success"] == 3
    assert data["mirrors"]["failed"] == 1
    assert data["mirrors"]["health_percentage"] == 75.0


@pytest.mark.asyncio
async def test_detailed_health_unhealthy_when_most_mirrors_fail(client, db_session, monkeypatch):
    """Test health check is unhealthy when most mirrors fail."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mostly failed mirrors (< 50% health)
    for i in range(4):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=i,
            source_project_path=f"group/project-{i}",
            target_project_id=i + 100,
            target_project_path=f"group/project-{i}-mirror",
            enabled=True,
            last_update_status="success" if i == 0 else "failed"
        )
        db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    # Should be unhealthy (< 50% success rate)
    assert data["status"] == "unhealthy"
    assert data["mirrors"]["health_percentage"] == 25.0


@pytest.mark.asyncio
async def test_detailed_health_with_disabled_mirrors(client, db_session, monkeypatch):
    """Test health check properly counts disabled mirrors."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mix of enabled and disabled mirrors
    for i in range(4):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=i,
            source_project_path=f"group/project-{i}",
            target_project_id=i + 100,
            target_project_path=f"group/project-{i}-mirror",
            enabled=(i < 2),  # First 2 enabled, last 2 disabled
            last_update_status="success"
        )
        db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["mirrors"]["total"] == 4
    assert data["mirrors"]["enabled"] == 2
    assert data["mirrors"]["disabled"] == 2


@pytest.mark.asyncio
async def test_detailed_health_with_expiring_tokens(client, db_session, monkeypatch):
    """Test health check detects tokens expiring soon."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror with token expiring in 15 days (within 30 day warning)
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="group/project",
        target_project_id=2,
        target_project_path="group/project-mirror",
        enabled=True,
        last_update_status="success",
        encrypted_mirror_token=encryption.encrypt("token"),
        mirror_token_expires_at=datetime.utcnow() + timedelta(days=15)
    )
    db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "degraded"
    assert data["tokens"]["total_with_tokens"] == 1
    assert data["tokens"]["expiring_soon"] == 1
    assert data["tokens"]["expired"] == 0

    # Check token component message
    token_component = next(c for c in data["components"] if c["name"] == "tokens")
    assert token_component["status"] == "degraded"
    assert "expiring" in token_component["message"].lower()


@pytest.mark.asyncio
async def test_detailed_health_with_expired_tokens(client, db_session, monkeypatch):
    """Test health check detects expired tokens."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror with expired token
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="group/project",
        target_project_id=2,
        target_project_path="group/project-mirror",
        enabled=True,
        last_update_status="success",
        encrypted_mirror_token=encryption.encrypt("token"),
        mirror_token_expires_at=datetime.utcnow() - timedelta(days=5)
    )
    db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    # Expired tokens cause degraded status (not unhealthy)
    assert data["status"] == "degraded"
    assert data["tokens"]["expired"] == 1
    assert data["tokens"]["active"] == 0

    # Check token component message
    token_component = next(c for c in data["components"] if c["name"] == "tokens")
    assert token_component["status"] == "unhealthy"
    assert "expired" in token_component["message"].lower()


@pytest.mark.asyncio
async def test_detailed_health_with_active_tokens(client, db_session, monkeypatch):
    """Test health check with active tokens (not expiring soon)."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror with token expiring in 60 days (well beyond 30 day warning)
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="group/project",
        target_project_id=2,
        target_project_path="group/project-mirror",
        enabled=True,
        last_update_status="success",
        encrypted_mirror_token=encryption.encrypt("token"),
        mirror_token_expires_at=datetime.utcnow() + timedelta(days=60)
    )
    db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["tokens"]["total_with_tokens"] == 1
    assert data["tokens"]["active"] == 1
    assert data["tokens"]["expiring_soon"] == 0
    assert data["tokens"]["expired"] == 0


@pytest.mark.asyncio
async def test_detailed_health_with_instance_check(client, db_session, monkeypatch):
    """Test health check with instance connectivity check."""
    # Create instance
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    # Mock GitLabClient to return successful connection
    with patch('app.api.health.GitLabClient') as MockClient:
        mock_client = MagicMock()
        mock_client.test_connection.return_value = {"id": 1, "username": "test"}
        MockClient.return_value = mock_client

        response = await client.get("/api/health?check_instances=true")
        assert response.status_code == 200
        data = response.json()

        assert data["instances"] is not None
        assert len(data["instances"]) == 1
        assert data["instances"][0]["name"] == "Test Instance"
        assert data["instances"][0]["status"] == "healthy"
        assert data["instances"][0]["latency_ms"] is not None

        # Should have gitlab_instances component
        instances_component = next(
            (c for c in data["components"] if c["name"] == "gitlab_instances"),
            None
        )
        assert instances_component is not None
        assert instances_component["status"] == "healthy"


@pytest.mark.asyncio
async def test_detailed_health_with_unreachable_instance(client, db_session, monkeypatch):
    """Test health check when GitLab instance is unreachable."""
    # Create instance
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    # Mock GitLabClient to raise connection error
    with patch('app.api.health.GitLabClient') as MockClient:
        MockClient.return_value.test_connection.side_effect = Exception(
            "Connection timeout: unable to reach server"
        )

        response = await client.get("/api/health?check_instances=true")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "degraded"
        assert data["instances"] is not None
        assert len(data["instances"]) == 1
        assert data["instances"][0]["status"] == "unreachable"
        assert "timeout" in data["instances"][0]["error"].lower()


@pytest.mark.asyncio
async def test_detailed_health_with_auth_failed_instance(client, db_session, monkeypatch):
    """Test health check when GitLab instance auth fails."""
    # Create instance
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    # Mock GitLabClient to raise auth error
    with patch('app.api.health.GitLabClient') as MockClient:
        MockClient.return_value.test_connection.side_effect = Exception(
            "401 Unauthorized: Invalid token"
        )

        response = await client.get("/api/health?check_instances=true")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "degraded"
        assert data["instances"][0]["status"] == "auth_failed"


@pytest.mark.asyncio
async def test_detailed_health_without_instance_check(client, db_session, monkeypatch):
    """Test health check without instance connectivity check (default)."""
    # Create instance
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    # Should not include instance health when check_instances=false (default)
    assert data["instances"] is None

    # Should not have gitlab_instances component
    instances_component = next(
        (c for c in data["components"] if c["name"] == "gitlab_instances"),
        None
    )
    assert instances_component is None


@pytest.mark.asyncio
async def test_detailed_health_pending_mirrors(client, db_session, monkeypatch):
    """Test health check with pending mirrors."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create pending mirror
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="group/project",
        target_project_id=2,
        target_project_path="group/project-mirror",
        enabled=True,
        last_update_status="pending"
    )
    db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    # Pending mirrors should not affect health (they haven't synced yet)
    assert data["status"] == "healthy"
    assert data["mirrors"]["pending"] == 1
    assert data["mirrors"]["health_percentage"] == 100.0


@pytest.mark.asyncio
async def test_detailed_health_unknown_status_mirrors(client, db_session, monkeypatch):
    """Test health check with mirrors that have unknown status (null)."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror with no status yet (null)
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="group/project",
        target_project_id=2,
        target_project_path="group/project-mirror",
        enabled=True,
        last_update_status=None
    )
    db_session.add(mirror)
    await db_session.commit()

    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["mirrors"]["unknown"] == 1
    assert data["mirrors"]["health_percentage"] == 100.0  # No synced mirrors to calculate from


@pytest.mark.asyncio
async def test_legacy_health_endpoint(client):
    """Test legacy /health endpoint for backward compatibility."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_health_response_includes_version(client):
    """Test health response includes application version."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert "version" in data
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_health_database_latency_is_measured(client):
    """Test that database latency is measured and reported."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    db_component = next(c for c in data["components"] if c["name"] == "database")
    assert db_component["latency_ms"] is not None
    assert db_component["latency_ms"] >= 0
