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
async def sample_mirror(db_session, sample_pair, sample_instances):
    """Create sample mirror."""
    source, target = sample_instances
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
    # Return mirror with instance IDs for convenience
    mirror._source_instance_id = source.id
    mirror._target_instance_id = target.id
    return mirror


@pytest.mark.asyncio
async def test_no_conflict_when_no_jobs(db_session, sample_mirror):
    """Test that no conflict is detected when there are no running jobs."""
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=100,
        target_project_id=200,
        source_instance_id=sample_mirror._source_instance_id,
        target_instance_id=sample_mirror._target_instance_id
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
        target_project_id=200,
        source_instance_id=sample_mirror._source_instance_id,
        target_instance_id=sample_mirror._target_instance_id
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

    # Create a running job 100→200 (with instance IDs for conflict detection)
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="running",
        source_project_id=100,
        target_project_id=200,
        source_instance_id=sample_mirror._source_instance_id,
        target_instance_id=sample_mirror._target_instance_id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Check for conflict trying to sync 200→100 (reverse direction)
    # Note: When checking reverse, we swap source/target instance IDs too
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100,
        source_instance_id=sample_mirror._target_instance_id,
        target_instance_id=sample_mirror._source_instance_id
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

    # Create a pending job 100→200 (with instance IDs for conflict detection)
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="pending",
        source_project_id=100,
        target_project_id=200,
        source_instance_id=sample_mirror._source_instance_id,
        target_instance_id=sample_mirror._target_instance_id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Check for conflict trying to sync 200→100 (reverse direction)
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=200,
        target_project_id=100,
        source_instance_id=sample_mirror._target_instance_id,
        target_instance_id=sample_mirror._source_instance_id
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
        target_project_id=100,
        source_instance_id=sample_mirror._target_instance_id,
        target_instance_id=sample_mirror._source_instance_id
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
        target_project_id=100,
        source_instance_id=sample_mirror._target_instance_id,
        target_instance_id=sample_mirror._source_instance_id
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
        source_instance_id=sample_mirror._target_instance_id,
        target_instance_id=sample_mirror._source_instance_id,
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
    # Using same instance IDs - projects are different but instances are the same
    conflict = await check_bidirectional_sync_conflict(
        db_session,
        source_project_id=300,
        target_project_id=400,
        source_instance_id=sample_mirror._source_instance_id,
        target_instance_id=sample_mirror._target_instance_id
    )
    assert conflict is None


# Tests for stale job cleanup

@pytest.mark.asyncio
async def test_cleanup_stale_running_job(db_session, sample_mirror):
    """Test that stale running jobs are marked as failed."""
    from app.core.issue_scheduler import cleanup_stale_jobs
    from app.config import settings

    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job that started long ago (beyond stale threshold)
    stale_time = datetime.utcnow() - timedelta(minutes=settings.stale_job_timeout_minutes + 10)
    stale_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="running",
        started_at=stale_time,
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(stale_job)
    await db_session.commit()
    await db_session.refresh(stale_job)

    # Run cleanup
    cleaned_count = await cleanup_stale_jobs(db_session)

    assert cleaned_count == 1
    await db_session.refresh(stale_job)
    assert stale_job.status == "failed"
    assert stale_job.completed_at is not None
    assert "stale" in stale_job.error_details["error"].lower()


@pytest.mark.asyncio
async def test_cleanup_stale_pending_job(db_session, sample_mirror):
    """Test that stale pending jobs are marked as failed."""
    from app.core.issue_scheduler import cleanup_stale_jobs
    from app.config import settings

    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a pending job that was created long ago (beyond stale threshold)
    stale_time = datetime.utcnow() - timedelta(minutes=settings.stale_job_timeout_minutes + 10)
    stale_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="pending",
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(stale_job)
    await db_session.commit()

    # Manually set created_at to stale time (since default is utcnow)
    stale_job.created_at = stale_time
    await db_session.commit()
    await db_session.refresh(stale_job)

    # Run cleanup
    cleaned_count = await cleanup_stale_jobs(db_session)

    assert cleaned_count == 1
    await db_session.refresh(stale_job)
    assert stale_job.status == "failed"


@pytest.mark.asyncio
async def test_cleanup_does_not_affect_recent_jobs(db_session, sample_mirror):
    """Test that recent jobs are not affected by cleanup."""
    from app.core.issue_scheduler import cleanup_stale_jobs

    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a running job that started recently
    recent_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="running",
        started_at=datetime.utcnow(),  # Just started
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(recent_job)
    await db_session.commit()
    await db_session.refresh(recent_job)

    # Run cleanup
    cleaned_count = await cleanup_stale_jobs(db_session)

    assert cleaned_count == 0
    await db_session.refresh(recent_job)
    assert recent_job.status == "running"  # Unchanged


@pytest.mark.asyncio
async def test_cleanup_does_not_affect_completed_jobs(db_session, sample_mirror):
    """Test that completed jobs are not affected by cleanup."""
    from app.core.issue_scheduler import cleanup_stale_jobs
    from app.config import settings

    config = MirrorIssueConfig(
        mirror_id=sample_mirror.id,
        enabled=True,
        sync_interval_minutes=15
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    # Create a completed job that was created long ago
    stale_time = datetime.utcnow() - timedelta(minutes=settings.stale_job_timeout_minutes + 10)
    completed_job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="scheduled",
        status="completed",
        started_at=stale_time,
        completed_at=stale_time + timedelta(minutes=5),
        source_project_id=sample_mirror.source_project_id,
        target_project_id=sample_mirror.target_project_id,
    )
    db_session.add(completed_job)
    await db_session.commit()
    await db_session.refresh(completed_job)

    # Run cleanup
    cleaned_count = await cleanup_stale_jobs(db_session)

    assert cleaned_count == 0
    await db_session.refresh(completed_job)
    assert completed_job.status == "completed"  # Unchanged
