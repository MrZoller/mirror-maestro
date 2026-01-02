"""Tests for robustness improvements in issue syncing.

Tests cover:
- Circuit breaker pattern
- Transaction safety with rollback
- Batched commits
- Resource cleanup
- Idempotency protection
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from app.core.rate_limiter import CircuitBreaker
from app.core.gitlab_client import GitLabClientError
from app.core.issue_sync import IssueSyncEngine
from app.models import (
    MirrorIssueConfig,
    Mirror,
    GitLabInstance,
    InstancePair,
    IssueMapping,
    CommentMapping,
    AttachmentMapping,
)


# -------------------------------------------------------------------------
# Circuit Breaker Tests
# -------------------------------------------------------------------------


def test_circuit_breaker_closed_state():
    """Test circuit breaker in normal CLOSED state."""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=5)

    def success_func():
        return "success"

    result = breaker.call(success_func)
    assert result == "success"
    assert breaker.state == "CLOSED"
    assert breaker.failure_count == 0


def test_circuit_breaker_opens_after_failures():
    """Test circuit breaker opens after threshold failures."""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=5)

    def failing_func():
        raise Exception("Service unavailable")

    # Trigger failures
    for i in range(3):
        with pytest.raises(Exception):
            breaker.call(failing_func)

    # Circuit should now be OPEN
    assert breaker.state == "OPEN"
    assert breaker.failure_count == 3


def test_circuit_breaker_blocks_when_open():
    """Test circuit breaker blocks requests when OPEN."""
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

    def failing_func():
        raise Exception("Service unavailable")

    # Trigger failures to open circuit
    for _ in range(2):
        with pytest.raises(Exception):
            breaker.call(failing_func)

    assert breaker.state == "OPEN"

    # Next call should be blocked without executing function
    with pytest.raises(Exception) as exc_info:
        breaker.call(failing_func)

    assert "Circuit breaker is OPEN" in str(exc_info.value)


def test_circuit_breaker_half_open_recovery():
    """Test circuit breaker attempts recovery after timeout."""
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

    def failing_func():
        raise Exception("Service unavailable")

    # Trigger failures to open circuit
    for _ in range(2):
        with pytest.raises(Exception):
            breaker.call(failing_func)

    assert breaker.state == "OPEN"

    # Wait for recovery timeout
    import time
    time.sleep(1.1)

    # Manually set last_failure_time to simulate timeout
    breaker.last_failure_time = datetime.utcnow() - timedelta(seconds=2)

    # Next call should attempt recovery (HALF_OPEN)
    def success_func():
        return "recovered"

    result = breaker.call(success_func)
    assert result == "recovered"
    assert breaker.state == "CLOSED"
    assert breaker.failure_count == 0


def test_circuit_breaker_half_open_to_open():
    """Test circuit breaker reopens if recovery fails."""
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

    call_count = 0

    def sometimes_failing():
        nonlocal call_count
        call_count += 1
        raise Exception("Still failing")

    # Open the circuit
    for _ in range(2):
        with pytest.raises(Exception):
            breaker.call(sometimes_failing)

    assert breaker.state == "OPEN"

    # Simulate timeout elapsed
    breaker.last_failure_time = datetime.utcnow() - timedelta(seconds=2)

    # Try recovery - should fail and reopen
    with pytest.raises(Exception):
        breaker.call(sometimes_failing)

    assert breaker.state == "OPEN"


def test_circuit_breaker_resets_count_on_success():
    """Test that successful calls reset failure count."""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=5)

    def sometimes_failing():
        return "success"

    # Partial failures (below threshold)
    def failing():
        raise Exception("Failed")

    with pytest.raises(Exception):
        breaker.call(failing)
    assert breaker.failure_count == 1

    # Success should reset
    result = breaker.call(sometimes_failing)
    assert result == "success"
    assert breaker.failure_count == 0
    assert breaker.state == "CLOSED"


def test_circuit_breaker_get_state():
    """Test circuit breaker state reporting."""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    state = breaker.get_state()
    assert state["state"] == "CLOSED"
    assert state["failure_count"] == 0
    assert state["recovery_timeout"] == 60
    assert state["last_failure_time"] is None


def test_circuit_breaker_specific_exception_type():
    """Test circuit breaker only triggers on specific exception type."""
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout=5,
        expected_exception=GitLabClientError
    )

    def gitlab_error():
        raise GitLabClientError("GitLab error")

    def other_error():
        raise ValueError("Different error")

    # GitLab errors should trigger breaker
    with pytest.raises(GitLabClientError):
        breaker.call(gitlab_error)
    assert breaker.failure_count == 1

    # Other errors should not trigger breaker
    with pytest.raises(ValueError):
        breaker.call(other_error)
    assert breaker.failure_count == 1  # Should not increment


# -------------------------------------------------------------------------
# Transaction Safety Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batched_comment_commits(db_session):
    """Test that comment syncing uses batched commits."""
    # This is an integration test - would need full setup
    # Testing the concept that multiple comments are committed together
    pass  # TODO: Add when DB fixtures are available


@pytest.mark.asyncio
async def test_batched_attachment_commits(db_session):
    """Test that attachment syncing uses batched commits."""
    # This is an integration test - would need full setup
    pass  # TODO: Add when DB fixtures are available


@pytest.mark.asyncio
async def test_partial_sync_status_on_failure(db_session):
    """Test that partial sync status is set when post-creation sync fails."""
    # This is an integration test - would need full setup
    pass  # TODO: Add when DB fixtures are available


# -------------------------------------------------------------------------
# Resource Cleanup Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_finds_orphaned_issues():
    """Test cleanup detects orphaned issues on target."""
    # Mock setup
    mock_db = AsyncMock()
    mock_config = Mock(spec=MirrorIssueConfig)
    mock_config.id = 1

    mock_mirror = Mock(spec=Mirror)
    mock_mirror.id = 1
    mock_mirror.target_project_id = 100

    mock_source = Mock(spec=GitLabInstance)
    mock_source.id = 1
    mock_source.url = "https://source.gitlab.com"

    mock_target = Mock(spec=GitLabInstance)
    mock_target.id = 2
    mock_target.url = "https://target.gitlab.com"

    mock_pair = Mock(spec=InstancePair)

    # Create engine
    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=mock_db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=mock_source,
            target_instance=mock_target,
            instance_pair=mock_pair
        )

        # Mock target issues fetch
        mock_target_issues = [
            {"id": 1001, "iid": 1, "description": "Issue 1"},
            {"id": 1002, "iid": 2, "description": "Issue 2"},
        ]

        engine._execute_gitlab_api_call = AsyncMock(return_value=mock_target_issues)

        # Mock DB query to return no mappings (orphaned)
        mock_result = Mock()
        mock_result.scalar_one_or_none = Mock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.delete = AsyncMock()

        # Run cleanup
        stats = await engine.cleanup_orphaned_resources()

        # Should find 2 orphaned issues
        assert stats["orphaned_issues_found"] == 2


@pytest.mark.asyncio
async def test_cleanup_deletes_orphaned_comment_mappings():
    """Test cleanup deletes orphaned comment mappings."""
    # Would need full DB setup to test properly
    pass  # TODO: Add when DB fixtures are available


@pytest.mark.asyncio
async def test_cleanup_deletes_orphaned_attachment_mappings():
    """Test cleanup deletes orphaned attachment mappings."""
    # Would need full DB setup to test properly
    pass  # TODO: Add when DB fixtures are available


# -------------------------------------------------------------------------
# Idempotency Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_existing_target_issue_found():
    """Test finding existing target issue by source reference."""
    mock_db = AsyncMock()
    mock_config = Mock(spec=MirrorIssueConfig)
    mock_mirror = Mock(spec=Mirror)
    mock_mirror.source_project_path = "group/project"
    mock_mirror.target_project_id = 100

    mock_source = Mock(spec=GitLabInstance)
    mock_source.id = 1
    mock_target = Mock(spec=GitLabInstance)
    mock_pair = Mock(spec=InstancePair)

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=mock_db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=mock_source,
            target_instance=mock_target,
            instance_pair=mock_pair
        )

        # Mock GitLab API to return existing issue
        existing_issue = {
            "id": 2001,
            "iid": 10,
            "description": "<!-- MIRROR_MAESTRO_FOOTER -->\n🔗 **Source**: [group/project#123]"
        }

        engine._execute_gitlab_api_call = AsyncMock(return_value=[existing_issue])

        # Search for existing issue
        found = await engine._find_existing_target_issue(
            source_issue_id=123,
            source_issue_iid=123
        )

        assert found is not None
        assert found["iid"] == 10


@pytest.mark.asyncio
async def test_find_existing_target_issue_not_found():
    """Test that search returns None when no match found."""
    mock_db = AsyncMock()
    mock_config = Mock(spec=MirrorIssueConfig)
    mock_mirror = Mock(spec=Mirror)
    mock_mirror.source_project_path = "group/project"
    mock_mirror.target_project_id = 100

    mock_source = Mock(spec=GitLabInstance)
    mock_source.id = 1
    mock_target = Mock(spec=GitLabInstance)
    mock_pair = Mock(spec=InstancePair)

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=mock_db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=mock_source,
            target_instance=mock_target,
            instance_pair=mock_pair
        )

        # Mock GitLab API to return issues without matching reference
        engine._execute_gitlab_api_call = AsyncMock(return_value=[
            {"id": 2001, "iid": 10, "description": "Different issue"}
        ])

        # Search for existing issue
        found = await engine._find_existing_target_issue(
            source_issue_id=999,
            source_issue_iid=999
        )

        assert found is None


@pytest.mark.asyncio
async def test_find_existing_handles_search_failure():
    """Test that find_existing returns None on search failure."""
    mock_db = AsyncMock()
    mock_config = Mock(spec=MirrorIssueConfig)
    mock_mirror = Mock(spec=Mirror)
    mock_source = Mock(spec=GitLabInstance)
    mock_source.id = 1
    mock_target = Mock(spec=GitLabInstance)
    mock_pair = Mock(spec=InstancePair)

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=mock_db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=mock_source,
            target_instance=mock_target,
            instance_pair=mock_pair
        )

        # Mock API call to raise exception
        engine._execute_gitlab_api_call = AsyncMock(
            side_effect=Exception("API Error")
        )

        # Should handle gracefully and return None
        found = await engine._find_existing_target_issue(
            source_issue_id=123,
            source_issue_iid=123
        )

        assert found is None


# -------------------------------------------------------------------------
# Integration Tests (require full DB setup)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_issue_with_idempotency_check():
    """Test that _create_target_issue checks for existing issues first."""
    # Would need full integration test with mocked GitLab client
    pass  # TODO: Add when full fixtures are available


@pytest.mark.asyncio
async def test_transaction_rollback_on_comment_sync_failure():
    """Test that failed comment sync triggers proper rollback."""
    # Would need full integration test
    pass  # TODO: Add when full fixtures are available
