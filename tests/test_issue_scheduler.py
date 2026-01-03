"""Tests for issue scheduler and conflict detection."""

import pytest
from datetime import datetime, timedelta

from app.models import (
    GitLabInstance,
    InstancePair,
    Mirror,
    MirrorIssueConfig,
    IssueSyncJob,
)
from app.core.issue_scheduler import check_bidirectional_sync_conflict


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
async def test_no_conflict_when_no_jobs(db_session, sample_mirror):
    """Test that no conflict is detected when there are no running jobs."""
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=100,
        target_project_id=200
    )
    assert conflict is None


@pytest.mark.asyncio
async def test_no_conflict_when_same_direction(db_session, sample_mirror):
    """Test that no conflict is detected for jobs in the same direction."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()

    # Check for conflict trying to sync 100→200 (same direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=100,
        target_project_id=200
    )
    # No conflict because it's the same direction, not reverse
    assert conflict is None


@pytest.mark.asyncio
async def test_conflict_detected_for_reverse_direction(db_session, sample_mirror):
    """Test that conflict is detected for jobs in reverse direction."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Check for conflict trying to sync 200→100 (reverse direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100
    )
    assert conflict is not None
    assert conflict.id == job.id


@pytest.mark.asyncio
async def test_conflict_detected_for_pending_job(db_session, sample_mirror):
    """Test that conflict is detected for pending jobs too."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a pending job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="pending",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Check for conflict trying to sync 200→100 (reverse direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100
    )
    assert conflict is not None
    assert conflict.id == job.id


@pytest.mark.asyncio
async def test_no_conflict_for_completed_job(db_session, sample_mirror):
    """Test that completed jobs don't cause conflicts."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a completed job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="completed",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()

    # Check for conflict trying to sync 200→100 (reverse direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100
    )
    # No conflict because the job is completed
    assert conflict is None


@pytest.mark.asyncio
async def test_no_conflict_for_failed_job(db_session, sample_mirror):
    """Test that failed jobs don't cause conflicts."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a failed job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="failed",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()

    # Check for conflict trying to sync 200→100 (reverse direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100
    )
    # No conflict because the job is failed
    assert conflict is None


@pytest.mark.asyncio
async def test_exclude_config_id(db_session, sample_mirror):
    """Test that exclude_config_id parameter works correctly."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Check for conflict with exclude_config_id matching the job's config
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100,
        exclude_config_id=config.id
    )
    # No conflict because we excluded this config
    assert conflict is None


@pytest.mark.asyncio
async def test_no_conflict_for_different_projects(db_session, sample_mirror):
    """Test that jobs on different projects don't conflict."""
    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job 100→200
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=100,
        target_project_id=200,
    )
    db_session.add(job)
    await db_session.commit()

    # Check for conflict trying to sync 300→400 (completely different projects)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=300,
        target_project_id=400
    )
    assert conflict is None
