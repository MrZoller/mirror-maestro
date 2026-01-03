"""Tests for issue mirroring API endpoints."""

import pytest
from datetime import datetime
from sqlalchemy import select

from app.models import (
    GitLabInstance,
    InstancePair,
    Mirror,
    MirrorIssueConfig,
    IssueMapping,
    IssueSyncJob,
)


@pytest.fixture
async def sample_instances(db_session):
    """Create sample GitLab instances."""
    source = GitLabInstance(
        name="Source GitLab",
        url="https://gitlab-source.example.com",
        encrypted_token="enc:source-token"
    )
    target = GitLabInstance(
        name="Target GitLab",
        url="https://gitlab-target.example.com",
        encrypted_token="enc:target-token"
    )
    db_session.add_all([source, target])
    await db_session.commit()
    await db_session.refresh(source)
    await db_session.refresh(target)
    return source, target


@pytest.fixture
async def sample_pair(db_session, sample_instances):
    """Create sample instance pair."""
    source, target = sample_instances
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source.id,
        target_instance_id=target.id,
        mirror_direction="pull"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)
    return pair


@pytest.fixture
async def sample_mirror(db_session, sample_pair):
    """Create sample mirror."""
    mirror = Mirror(
        instance_pair_id=sample_pair.id,
        source_project_id=100,
        source_project_path="group/source-project",
        target_project_id=200,
        target_project_path="group/target-project"
    )
    db_session.add(mirror)
    await db_session.commit()
    await db_session.refresh(mirror)
    return mirror


@pytest.mark.asyncio
async def test_create_issue_mirror_config(client, sample_mirror):
    """Test creating an issue mirror configuration."""
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_comments": True,
        "sync_labels": True,
        "sync_attachments": True,
        "sync_weight": True,
        "sync_time_estimate": True,
        "sync_time_spent": True,
        "sync_closed_issues": False,
        "update_existing": True,
        "sync_existing_issues": False,
        "sync_interval_minutes": 15
    })

    assert response.status_code == 201
    data = response.json()
    assert data["mirror_id"] == sample_mirror.id
    assert data["enabled"] is True
    assert data["sync_comments"] is True
    assert data["sync_existing_issues"] is False
    assert data["sync_interval_minutes"] == 15


@pytest.mark.asyncio
async def test_create_duplicate_issue_mirror_config(client, sample_mirror, db_session):
    """Test creating duplicate config fails."""
    # Create first config
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()

    # Try to create duplicate
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_interval_minutes": 15
    })

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_issue_mirror_config(client, sample_mirror, db_session):
    """Test retrieving an issue mirror configuration."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_comments=True,
        sync_labels=True,
        sync_attachments=False,
        sync_weight=True,
        sync_time_estimate=True,
        sync_time_spent=False,
        sync_closed_issues=False,
        update_existing=True,
        sync_existing_issues=False,
        sync_interval_minutes=30
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    response = await client.get(f"/api/issue-mirrors/{config.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == config.id
    assert data["mirror_id"] == sample_mirror.id
    assert data["enabled"] is True
    assert data["sync_comments"] is True
    assert data["sync_attachments"] is False
    assert data["sync_time_spent"] is False
    assert data["sync_existing_issues"] is False
    assert data["sync_interval_minutes"] == 30


@pytest.mark.asyncio
async def test_get_nonexistent_issue_mirror_config(client):
    """Test retrieving non-existent config returns 404."""
    response = await client.get("/api/issue-mirrors/9999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_issue_mirror_configs(client, sample_mirror, db_session):
    """Test listing all issue mirror configurations."""
    # Create multiple configs (using different mirrors)
    config1 = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config1)
    await db_session.commit()

    response = await client.get("/api/issue-mirrors")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(c["id"] == config1.id for c in data)


@pytest.mark.asyncio
async def test_update_issue_mirror_config(client, sample_mirror, db_session):
    """Test updating an issue mirror configuration."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_comments=True,
        sync_existing_issues=False,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    response = await client.put(f"/api/issue-mirrors/{config.id}", json={
        "enabled": False,
        "sync_comments": False,
        "sync_existing_issues": True,
        "sync_interval_minutes": 30
    })

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["sync_comments"] is False
    assert data["sync_existing_issues"] is True
    assert data["sync_interval_minutes"] == 30


@pytest.mark.asyncio
async def test_update_nonexistent_config(client):
    """Test updating non-existent config returns 404."""
    response = await client.put("/api/issue-mirrors/9999", json={
        "enabled": False
    })
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_issue_mirror_config(client, sample_mirror, db_session):
    """Test deleting an issue mirror configuration."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    response = await client.delete(f"/api/issue-mirrors/{config.id}")
    assert response.status_code == 204

    # Verify deletion
    result = await db_session.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_nonexistent_config(client):
    """Test deleting non-existent config returns 404."""
    response = await client.delete("/api/issue-mirrors/9999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_trigger_sync_creates_job(client, sample_mirror, db_session):
    """Test triggering sync creates a sync job."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Mock the GitLab clients to avoid actual API calls
    from unittest.mock import patch, AsyncMock

    with patch('app.core.issue_sync.IssueSyncEngine') as MockEngine:
        mock_engine = AsyncMock()
        mock_engine.sync.return_value = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": []
        }
        MockEngine.return_value = mock_engine

        response = await client.post(f"/api/issue-mirrors/{config.id}/trigger-sync")

        assert response.status_code == 202
        data = response.json()
        assert data["message"] == "Sync triggered"
        assert data["config_id"] == config.id
        assert "job_id" in data

        # Give background task a moment to start
        import asyncio
        await asyncio.sleep(0.1)

        # Verify job was created
        result = await db_session.execute(
            select(IssueSyncJob).where(IssueSyncJob.mirror_issue_config_id == config.id)
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.job_type == "manual"


@pytest.mark.asyncio
async def test_trigger_sync_disabled_config(client, sample_mirror, db_session):
    """Test triggering sync on disabled config fails."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=False,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    response = await client.post(f"/api/issue-mirrors/{config.id}/trigger-sync")

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_trigger_sync_nonexistent_config(client):
    """Test triggering sync on non-existent config returns 404."""
    response = await client.post("/api/issue-mirrors/9999/trigger-sync")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_issue_mirror_config_validation(client, sample_mirror):
    """Test validation of issue mirror configuration."""
    # Test invalid sync interval (too low)
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_interval_minutes": 2  # Below minimum of 5
    })
    assert response.status_code == 422

    # Test invalid sync interval (too high)
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_interval_minutes": 2000  # Above maximum of 1440
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_config_by_mirror_id(client, sample_mirror, db_session):
    """Test retrieving config by mirror ID."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    response = await client.get(f"/api/issue-mirrors?mirror_id={sample_mirror.id}")

    assert response.status_code == 200
    data = response.json()
    # Should return a list
    assert isinstance(data, list)
    # Find our config
    our_config = next((c for c in data if c["mirror_id"] == sample_mirror.id), None)
    assert our_config is not None
    assert our_config["id"] == config.id


@pytest.mark.asyncio
async def test_sync_existing_issues_default_false(client, sample_mirror):
    """Test that sync_existing_issues defaults to False."""
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_interval_minutes": 15
        # Not specifying sync_existing_issues
    })

    assert response.status_code == 201
    data = response.json()
    assert data["sync_existing_issues"] is False


@pytest.mark.asyncio
async def test_sync_existing_issues_can_be_enabled(client, sample_mirror):
    """Test that sync_existing_issues can be explicitly enabled."""
    response = await client.post("/api/issue-mirrors", json={
        "mirror_id": sample_mirror.id,
        "enabled": True,
        "sync_interval_minutes": 15,
        "sync_existing_issues": True
    })

    assert response.status_code == 201
    data = response.json()
    assert data["sync_existing_issues"] is True


@pytest.mark.asyncio
async def test_trigger_sync_conflict_same_config(client, sample_mirror, db_session):
    """Test that triggering sync fails when a sync is already in progress for the same config."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job for this config
    running_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(running_job)
    await db_session.commit()
    await db_session.refresh(running_job)

    # Try to trigger another sync - should fail
    response = await client.post(f"/api/issue-mirrors/{config.id}/trigger-sync")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"].lower()
    assert str(running_job.id) in response.json()["detail"]


@pytest.mark.asyncio
async def test_trigger_sync_conflict_pending_job(client, sample_mirror, db_session):
    """Test that triggering sync fails when a sync is pending for the same config."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a pending job for this config
    pending_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="pending",
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(pending_job)
    await db_session.commit()
    await db_session.refresh(pending_job)

    # Try to trigger another sync - should fail
    response = await client.post(f"/api/issue-mirrors/{config.id}/trigger-sync")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"].lower()


@pytest.fixture
async def reverse_mirror_setup(db_session, sample_instances):
    """Create reverse mirror setup for bidirectional conflict testing.

    Creates:
    - pair1: source→target (pull direction, meaning issues flow from source to target)
    - pair2: target→source (pull direction, meaning issues flow from target to source)
    - mirror1: source_project_id=100 → target_project_id=200
    - mirror2: source_project_id=200 → target_project_id=100 (reverse)
    """
    source, target = sample_instances

    # First pair: source→target
    pair1 = InstancePair(
        name="Pair A to B",
        source_instance_id=source.id,
        target_instance_id=target.id,
        mirror_direction="pull"
    )
    db_session.add(pair1)
    await db_session.commit()
    await db_session.refresh(pair1)

    # Second pair: target→source (reverse direction)
    pair2 = InstancePair(
        name="Pair B to A",
        source_instance_id=target.id,
        target_instance_id=source.id,
        mirror_direction="pull"
    )
    db_session.add(pair2)
    await db_session.commit()
    await db_session.refresh(pair2)

    # Mirror 1: project 100 → 200
    mirror1 = Mirror(
        instance_pair_id=pair1.id,
        source_project_id=100,
        source_project_path="group/project-a",
        target_project_id=200,
        target_project_path="group/project-b"
    )
    db_session.add(mirror1)
    await db_session.commit()
    await db_session.refresh(mirror1)

    # Mirror 2: project 200 → 100 (reverse of mirror1)
    mirror2 = Mirror(
        instance_pair_id=pair2.id,
        source_project_id=200,
        source_project_path="group/project-b",
        target_project_id=100,
        target_project_path="group/project-a"
    )
    db_session.add(mirror2)
    await db_session.commit()
    await db_session.refresh(mirror2)

    return {
        "pair1": pair1,
        "pair2": pair2,
        "mirror1": mirror1,
        "mirror2": mirror2,
        "source_instance": source,
        "target_instance": target,
    }


@pytest.mark.asyncio
async def test_trigger_sync_bidirectional_conflict(client, reverse_mirror_setup, db_session):
    """Test that bidirectional sync conflict is detected.

    When A→B sync is running, attempting to start B→A sync should fail.
    """
    mirror1 = reverse_mirror_setup["mirror1"]  # 100→200
    mirror2 = reverse_mirror_setup["mirror2"]  # 200→100 (reverse)

    # Create configs for both mirrors
    config1 = MirrorIssueConfig(
        mirror_id=mirror1.id,
        enabled=True,
        sync_interval_minutes=15
    )
    config2 = MirrorIssueConfig(
        mirror_id=mirror2.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add_all([config1, config2])
    await db_session.commit()
    await db_session.refresh(config1)
    await db_session.refresh(config2)

    # Start a sync job for mirror1 (100→200)
    running_job = IssueSyncJob(
        mirror_issue_config_id=config1.id,
        job_type="manual",
        status="running",
        source_project_id=mirror1.source_project_id,  # 100
        target_project_id=mirror1.target_project_id,  # 200
    )
    db_session.add(running_job)
    await db_session.commit()
    await db_session.refresh(running_job)

    # Try to trigger sync for mirror2 (200→100) - should fail due to bidirectional conflict
    response = await client.post(f"/api/issue-mirrors/{config2.id}/trigger-sync")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "bidirectional sync conflict" in detail.lower()
    assert str(running_job.id) in detail


@pytest.mark.asyncio
async def test_trigger_sync_no_conflict_different_projects(client, db_session, sample_instances):
    """Test that syncs on different projects don't conflict."""
    source, target = sample_instances

    # Create pair
    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source.id,
        target_instance_id=target.id,
        mirror_direction="pull"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create two mirrors for different projects
    mirror1 = Mirror(
        instance_pair_id=pair.id,
        source_project_id=100,
        source_project_path="group/project-a",
        target_project_id=200,
        target_project_path="group/project-b"
    )
    mirror2 = Mirror(
        instance_pair_id=pair.id,
        source_project_id=300,
        source_project_path="group/project-c",
        target_project_id=400,
        target_project_path="group/project-d"
    )
    db_session.add_all([mirror1, mirror2])
    await db_session.commit()
    await db_session.refresh(mirror1)
    await db_session.refresh(mirror2)

    # Create configs for both mirrors
    config1 = MirrorIssueConfig(
        mirror_id=mirror1.id,
        enabled=True,
        sync_interval_minutes=15
    )
    config2 = MirrorIssueConfig(
        mirror_id=mirror2.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add_all([config1, config2])
    await db_session.commit()
    await db_session.refresh(config1)
    await db_session.refresh(config2)

    # Start a sync job for mirror1 (100→200)
    running_job = IssueSyncJob(
        mirror_issue_config_id=config1.id,
        job_type="manual",
        status="running",
        source_project_id=mirror1.source_project_id,
        target_project_id=mirror1.target_project_id,
    )
    db_session.add(running_job)
    await db_session.commit()

    # Triggering sync for mirror2 (300→400) should succeed since it's different projects
    from unittest.mock import patch, AsyncMock

    with patch('app.core.issue_sync.IssueSyncEngine') as MockEngine:
        mock_engine = AsyncMock()
        mock_engine.sync.return_value = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": []
        }
        MockEngine.return_value = mock_engine

        response = await client.post(f"/api/issue-mirrors/{config2.id}/trigger-sync")

        # Should succeed - no conflict with different projects
        assert response.status_code == 202
        assert "job_id" in response.json()


@pytest.mark.asyncio
async def test_trigger_sync_completed_job_no_conflict(client, sample_mirror, db_session):
    """Test that completed jobs don't block new syncs."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a completed job for this config
    completed_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="completed",
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(completed_job)
    await db_session.commit()

    # Triggering sync should succeed since previous job is completed
    from unittest.mock import patch, AsyncMock

    with patch('app.core.issue_sync.IssueSyncEngine') as MockEngine:
        mock_engine = AsyncMock()
        mock_engine.sync.return_value = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": []
        }
        MockEngine.return_value = mock_engine

        response = await client.post(f"/api/issue-mirrors/{config.id}/trigger-sync")

        assert response.status_code == 202
        assert "job_id" in response.json()
