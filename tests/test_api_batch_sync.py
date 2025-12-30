"""Tests for batch mirror sync functionality."""

import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InstancePair, Mirror, GitLabInstance
from app.core.encryption import encryption


@pytest.mark.asyncio
async def test_sync_all_mirrors_success(client: AsyncClient, db_session: AsyncSession):
    """Test successful batch sync of all mirrors in a pair."""
    # Create instances
    source_instance = GitLabInstance(
        name="Source GitLab",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("source-token"),
        api_user_id=1,
        api_username="source-user"
    )
    target_instance = GitLabInstance(
        name="Target GitLab",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("target-token"),
        api_user_id=2,
        api_username="target-user"
    )
    db_session.add(source_instance)
    db_session.add(target_instance)
    await db_session.commit()
    await db_session.refresh(source_instance)
    await db_session.refresh(target_instance)

    # Create pair
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source_instance.id,
        target_instance_id=target_instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create test mirrors
    mirrors = []
    for i in range(3):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=100 + i,
            source_project_path=f"source/project-{i}",
            target_project_id=200 + i,
            target_project_path=f"target/project-{i}",
            mirror_id=300 + i,
            enabled=True,
            last_update_status="success"
        )
        mirrors.append(mirror)
        db_session.add(mirror)

    await db_session.commit()

    # Mock GitLab client
    with patch('app.api.pairs.GitLabClient') as MockClient:
        mock_client = MagicMock()
        mock_client.trigger_mirror_update.return_value = {"status": "success"}
        MockClient.return_value = mock_client

        # Trigger batch sync
        response = await client.post(f"/api/pairs/{pair.id}/sync-mirrors")

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "completed"
        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert "duration_seconds" in result
        assert "operations_per_second" in result

        # Verify GitLab client was called for each mirror
        assert mock_client.trigger_mirror_update.call_count == 3


@pytest.mark.asyncio
async def test_sync_all_mirrors_no_enabled_mirrors(client: AsyncClient, db_session: AsyncSession):
    """Test batch sync when there are no enabled mirrors."""
    # Create instances
    source_instance = GitLabInstance(
        name="Source GitLab",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("source-token")
    )
    target_instance = GitLabInstance(
        name="Target GitLab",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("target-token")
    )
    db_session.add(source_instance)
    db_session.add(target_instance)
    await db_session.commit()
    await db_session.refresh(source_instance)
    await db_session.refresh(target_instance)

    # Create pair
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source_instance.id,
        target_instance_id=target_instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create disabled mirror
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=100,
        source_project_path="source/project",
        target_project_id=200,
        target_project_path="target/project",
        mirror_id=300,
        enabled=False  # Disabled
    )
    db_session.add(mirror)
    await db_session.commit()

    # Trigger batch sync
    response = await client.post(f"/api/pairs/{pair.id}/sync-mirrors")

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["total"] == 0
    assert result["succeeded"] == 0
    assert result["message"] == "No enabled mirrors found for this pair"


@pytest.mark.asyncio
async def test_sync_all_mirrors_skip_unconfigured(client: AsyncClient, db_session: AsyncSession):
    """Test batch sync skips mirrors not configured in GitLab."""
    # Create instances
    source_instance = GitLabInstance(
        name="Source GitLab",
        url="https://source.gitlab.com",
        encrypted_token="enc:token1",  # Use simple encrypted format instead of encryption.encrypt()
        api_user_id=1,
        api_username="source-user"
    )
    target_instance = GitLabInstance(
        name="Target GitLab",
        url="https://target.gitlab.com",
        encrypted_token="enc:token2",  # Use simple encrypted format instead of encryption.encrypt()
        api_user_id=2,
        api_username="target-user"
    )
    db_session.add(source_instance)
    db_session.add(target_instance)
    await db_session.commit()
    await db_session.refresh(source_instance)
    await db_session.refresh(target_instance)

    # Create pair
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source_instance.id,
        target_instance_id=target_instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create mirror without mirror_id (not configured in GitLab)
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=100,
        source_project_path="source/project",
        target_project_id=200,
        target_project_path="target/project",
        mirror_id=None,  # Not configured in GitLab
        enabled=True
    )
    db_session.add(mirror)
    await db_session.commit()

    # Mock GitLabClient (not actually called for unconfigured mirrors, but needed for safety)
    with patch('app.api.pairs.GitLabClient') as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client

        # Trigger batch sync
        response = await client.post(f"/api/pairs/{pair.id}/sync-mirrors")

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "completed"
        assert result["total"] == 1
        assert result["succeeded"] == 1
        assert result["skipped"] == 1  # Should skip unconfigured mirror


@pytest.mark.asyncio
async def test_sync_all_mirrors_partial_failure(client: AsyncClient, db_session: AsyncSession):
    """Test batch sync with some mirrors failing."""
    # Create instances
    source_instance = GitLabInstance(
        name="Source GitLab",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("source-token")
    )
    target_instance = GitLabInstance(
        name="Target GitLab",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("target-token")
    )
    db_session.add(source_instance)
    db_session.add(target_instance)
    await db_session.commit()
    await db_session.refresh(source_instance)
    await db_session.refresh(target_instance)

    # Create pair
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source_instance.id,
        target_instance_id=target_instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create test mirrors
    mirror1 = Mirror(
        instance_pair_id=pair.id,
        source_project_id=100,
        source_project_path="source/project-1",
        target_project_id=200,
        target_project_path="target/project-1",
        mirror_id=300,
        enabled=True
    )
    mirror2 = Mirror(
        instance_pair_id=pair.id,
        source_project_id=101,
        source_project_path="source/project-2",
        target_project_id=201,
        target_project_path="target/project-2",
        mirror_id=301,
        enabled=True
    )
    db_session.add(mirror1)
    db_session.add(mirror2)
    await db_session.commit()

    # Mock GitLab client - first succeeds, second fails
    with patch('app.api.pairs.GitLabClient') as MockClient:
        mock_client = MagicMock()
        mock_client.trigger_mirror_update.side_effect = [
            {"status": "success"},
            Exception("GitLab API error")
        ]
        MockClient.return_value = mock_client

        # Trigger batch sync
        response = await client.post(f"/api/pairs/{pair.id}/sync-mirrors")

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "completed"
        assert result["total"] == 2
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "GitLab API error" in result["errors"][0]


@pytest.mark.asyncio
async def test_sync_all_mirrors_pair_not_found(client: AsyncClient):
    """Test batch sync with non-existent pair."""
    response = await client.post("/api/pairs/9999/sync-mirrors")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_sync_all_mirrors_pull_direction(client: AsyncClient, db_session: AsyncSession):
    """Test batch sync with pull mirrors (uses target instance)."""
    # Create instances
    source_instance = GitLabInstance(
        name="Source GitLab",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("source-token")
    )
    target_instance = GitLabInstance(
        name="Target GitLab",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("target-token")
    )
    db_session.add(source_instance)
    db_session.add(target_instance)
    await db_session.commit()
    await db_session.refresh(source_instance)
    await db_session.refresh(target_instance)

    # Create pair with pull direction
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source_instance.id,
        target_instance_id=target_instance.id,
        mirror_direction="pull"  # Pull direction
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create test mirror
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=100,
        source_project_path="source/project",
        target_project_id=200,
        target_project_path="target/project",
        mirror_id=300,
        enabled=True
    )
    db_session.add(mirror)
    await db_session.commit()

    # Mock GitLab client
    with patch('app.api.pairs.GitLabClient') as MockClient:
        mock_client = MagicMock()
        mock_client.trigger_mirror_update.return_value = {"status": "success"}
        MockClient.return_value = mock_client

        # Trigger batch sync
        response = await client.post(f"/api/pairs/{pair.id}/sync-mirrors")

        assert response.status_code == 200
        result = response.json()
        assert result["succeeded"] == 1

        # Verify GitLab client was created with TARGET instance (for pull)
        MockClient.assert_called_with(target_instance.url, target_instance.encrypted_token)
