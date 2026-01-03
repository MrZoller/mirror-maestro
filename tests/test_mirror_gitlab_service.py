"""Tests for the MirrorGitLabService robustness patterns."""

import pytest
import asyncio
from unittest.mock import MagicMock, patch

from app.core.mirror_gitlab_service import (
    MirrorGitLabService,
    get_mirror_gitlab_service,
    reset_mirror_gitlab_service,
)
from app.core.gitlab_client import (
    GitLabClientError,
    GitLabRateLimitError,
    GitLabConnectionError,
    GitLabServerError,
)


class MockGitLabClient:
    """Mock GitLab client for testing."""

    def __init__(self, url: str = "https://gitlab.example.com"):
        self.url = url
        self.call_count = 0

    def successful_operation(self):
        self.call_count += 1
        return {"success": True}

    def rate_limit_then_succeed(self):
        self.call_count += 1
        if self.call_count <= 2:
            raise GitLabRateLimitError("Rate limited")
        return {"success": True}

    def always_fail(self):
        self.call_count += 1
        raise GitLabConnectionError("Connection failed")

    def server_error_then_succeed(self):
        self.call_count += 1
        if self.call_count <= 1:
            raise GitLabServerError("Server error")
        return {"success": True}


@pytest.fixture(autouse=True)
def reset_service():
    """Reset the singleton before each test."""
    reset_mirror_gitlab_service()
    yield
    reset_mirror_gitlab_service()


@pytest.mark.asyncio
async def test_service_executes_successful_operation():
    """Test that successful operations work correctly."""
    service = MirrorGitLabService(delay_ms=0, max_retries=3)
    client = MockGitLabClient()

    result = await service.execute(
        client=client,
        operation=lambda c: c.successful_operation(),
        operation_name="test_op"
    )

    assert result == {"success": True}
    assert client.call_count == 1


@pytest.mark.asyncio
async def test_service_retries_on_rate_limit():
    """Test that rate limit errors trigger retry with backoff."""
    service = MirrorGitLabService(delay_ms=0, max_retries=3)
    client = MockGitLabClient()

    result = await service.execute(
        client=client,
        operation=lambda c: c.rate_limit_then_succeed(),
        operation_name="test_op"
    )

    assert result == {"success": True}
    # Should have called 3 times: 2 failures + 1 success
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_service_retries_on_server_error():
    """Test that server errors trigger retry."""
    service = MirrorGitLabService(delay_ms=0, max_retries=3)
    client = MockGitLabClient()

    result = await service.execute(
        client=client,
        operation=lambda c: c.server_error_then_succeed(),
        operation_name="test_op"
    )

    assert result == {"success": True}
    assert client.call_count == 2


@pytest.mark.asyncio
async def test_service_exhausts_retries():
    """Test that retries are exhausted on persistent failures."""
    service = MirrorGitLabService(delay_ms=0, max_retries=2)
    client = MockGitLabClient()

    with pytest.raises(GitLabConnectionError):
        await service.execute(
            client=client,
            operation=lambda c: c.always_fail(),
            operation_name="test_op"
        )

    # 1 initial + 2 retries = 3 attempts
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_failures():
    """Test that circuit breaker opens after threshold failures."""
    service = MirrorGitLabService(
        delay_ms=0,
        max_retries=0,  # No retries
        circuit_breaker_threshold=3,
        circuit_breaker_recovery=60
    )
    client = MockGitLabClient()

    # Trigger enough failures to open the circuit
    for _ in range(3):
        try:
            await service.execute(
                client=client,
                operation=lambda c: c.always_fail(),
                operation_name="test_op"
            )
        except GitLabConnectionError:
            pass

    # Circuit should now be open
    state = service.get_circuit_breaker_state(client.url)
    assert state["state"] == "OPEN"


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_when_open():
    """Test that open circuit breaker blocks requests."""
    service = MirrorGitLabService(
        delay_ms=0,
        max_retries=0,
        circuit_breaker_threshold=2,
        circuit_breaker_recovery=60
    )
    client = MockGitLabClient()

    # Open the circuit
    for _ in range(2):
        try:
            await service.execute(
                client=client,
                operation=lambda c: c.always_fail(),
                operation_name="test_op"
            )
        except GitLabConnectionError:
            pass

    # Next request should be blocked
    with pytest.raises(GitLabConnectionError) as exc_info:
        await service.execute(
            client=client,
            operation=lambda c: c.successful_operation(),
            operation_name="test_op"
        )

    assert "Circuit breaker is OPEN" in str(exc_info.value)


@pytest.mark.asyncio
async def test_batch_execution():
    """Test batch execution of operations."""
    service = MirrorGitLabService(delay_ms=0, max_retries=0)

    clients = [MockGitLabClient(f"https://gitlab{i}.example.com") for i in range(5)]
    operations = [
        {
            'client': client,
            'operation': lambda c: c.successful_operation(),
            'operation_name': f'op_{i}'
        }
        for i, client in enumerate(clients)
    ]

    tracker = await service.execute_batch(operations, batch_size=2)

    assert tracker.processed == 5
    assert tracker.succeeded == 5
    assert tracker.failed == 0


@pytest.mark.asyncio
async def test_batch_execution_with_failures():
    """Test batch execution with some failures."""
    service = MirrorGitLabService(delay_ms=0, max_retries=0)

    clients = [MockGitLabClient(f"https://gitlab{i}.example.com") for i in range(4)]
    operations = [
        {
            'client': clients[0],
            'operation': lambda c: c.successful_operation(),
            'operation_name': 'op_0'
        },
        {
            'client': clients[1],
            'operation': lambda c: c.always_fail(),
            'operation_name': 'op_1'
        },
        {
            'client': clients[2],
            'operation': lambda c: c.successful_operation(),
            'operation_name': 'op_2'
        },
        {
            'client': clients[3],
            'operation': lambda c: c.always_fail(),
            'operation_name': 'op_3'
        },
    ]

    tracker = await service.execute_batch(operations, batch_size=2)

    assert tracker.processed == 4
    assert tracker.succeeded == 2
    assert tracker.failed == 2


@pytest.mark.asyncio
async def test_service_metrics():
    """Test that service records metrics."""
    service = MirrorGitLabService(delay_ms=0, max_retries=0)
    client = MockGitLabClient()

    await service.execute(
        client=client,
        operation=lambda c: c.successful_operation(),
        operation_name="test_op"
    )

    metrics = service.get_metrics()
    assert metrics["operation_count"] >= 1


@pytest.mark.asyncio
async def test_reset_circuit_breaker():
    """Test manual circuit breaker reset."""
    service = MirrorGitLabService(
        delay_ms=0,
        max_retries=0,
        circuit_breaker_threshold=1
    )
    client = MockGitLabClient()

    # Open the circuit
    try:
        await service.execute(
            client=client,
            operation=lambda c: c.always_fail(),
            operation_name="test_op"
        )
    except GitLabConnectionError:
        pass

    assert service.get_circuit_breaker_state(client.url)["state"] == "OPEN"

    # Reset it
    result = service.reset_circuit_breaker(client.url)
    assert result is True
    assert service.get_circuit_breaker_state(client.url)["state"] == "CLOSED"


def test_singleton_service():
    """Test that get_mirror_gitlab_service returns a singleton."""
    reset_mirror_gitlab_service()

    service1 = get_mirror_gitlab_service()
    service2 = get_mirror_gitlab_service()

    assert service1 is service2


def test_reset_singleton():
    """Test that reset_mirror_gitlab_service creates a new instance."""
    service1 = get_mirror_gitlab_service()
    reset_mirror_gitlab_service()
    service2 = get_mirror_gitlab_service()

    assert service1 is not service2
