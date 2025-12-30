"""Tests for rate limiter functionality."""

import pytest
import asyncio
from datetime import datetime

from app.core.rate_limiter import RateLimiter, BatchOperationTracker


@pytest.mark.asyncio
async def test_rate_limiter_delay():
    """Test that rate limiter applies delay between operations."""
    rate_limiter = RateLimiter(delay_ms=100, max_retries=3)

    start_time = datetime.utcnow()
    await rate_limiter.delay()
    end_time = datetime.utcnow()

    elapsed_ms = (end_time - start_time).total_seconds() * 1000
    # Allow some tolerance for timing
    assert elapsed_ms >= 90  # At least 90ms (allow 10ms tolerance)


@pytest.mark.asyncio
async def test_rate_limiter_no_delay():
    """Test rate limiter with zero delay."""
    rate_limiter = RateLimiter(delay_ms=0, max_retries=3)

    start_time = datetime.utcnow()
    await rate_limiter.delay()
    end_time = datetime.utcnow()

    elapsed_ms = (end_time - start_time).total_seconds() * 1000
    assert elapsed_ms < 50  # Should be nearly instant


@pytest.mark.asyncio
async def test_rate_limiter_execute_success():
    """Test successful operation execution."""
    rate_limiter = RateLimiter(delay_ms=10, max_retries=3)

    def test_operation():
        return "success"

    result = await rate_limiter.execute_with_retry(test_operation, "test")
    assert result == "success"
    assert rate_limiter.operation_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_retry_on_rate_limit():
    """Test retry logic for rate limit errors."""
    rate_limiter = RateLimiter(delay_ms=10, max_retries=3)

    call_count = 0

    def test_operation():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("429 Too Many Requests")
        return "success"

    result = await rate_limiter.execute_with_retry(test_operation, "test")
    assert result == "success"
    assert call_count == 2  # Failed once, succeeded on retry


@pytest.mark.asyncio
async def test_rate_limiter_retry_exhausted():
    """Test that retries are exhausted after max attempts."""
    rate_limiter = RateLimiter(delay_ms=10, max_retries=2)

    def test_operation():
        raise Exception("429 Too Many Requests")

    with pytest.raises(Exception) as exc_info:
        await rate_limiter.execute_with_retry(test_operation, "test")

    assert "429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_limiter_non_rate_limit_error():
    """Test that non-rate-limit errors are not retried."""
    rate_limiter = RateLimiter(delay_ms=10, max_retries=3)

    call_count = 0

    def test_operation():
        nonlocal call_count
        call_count += 1
        raise ValueError("Some other error")

    with pytest.raises(ValueError):
        await rate_limiter.execute_with_retry(test_operation, "test")

    # Should fail immediately without retry
    assert call_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_metrics():
    """Test metrics tracking."""
    rate_limiter = RateLimiter(delay_ms=10, max_retries=3)
    rate_limiter.start_tracking()

    # Simulate some operations
    for _ in range(5):
        def test_op():
            return "ok"
        await rate_limiter.execute_with_retry(test_op, "test")
        await asyncio.sleep(0.01)  # Small delay between operations

    metrics = rate_limiter.get_metrics()
    assert metrics["operation_count"] == 5
    assert metrics["duration_seconds"] > 0
    assert metrics["operations_per_second"] > 0


def test_batch_tracker_success():
    """Test batch operation tracker for successful operations."""
    tracker = BatchOperationTracker(total_items=10)

    for _ in range(7):
        tracker.record_success()

    progress = tracker.get_progress()
    assert progress["total"] == 10
    assert progress["processed"] == 7
    assert progress["succeeded"] == 7
    assert progress["failed"] == 0
    assert progress["percent_complete"] == 70.0


def test_batch_tracker_mixed_results():
    """Test batch operation tracker with mixed results."""
    tracker = BatchOperationTracker(total_items=10)

    # Simulate mixed results
    for _ in range(6):
        tracker.record_success()
    for i in range(4):
        tracker.record_failure(f"Error {i}")

    progress = tracker.get_progress()
    assert progress["total"] == 10
    assert progress["processed"] == 10
    assert progress["succeeded"] == 6
    assert progress["failed"] == 4
    assert progress["percent_complete"] == 100.0

    summary = tracker.get_summary()
    assert summary["succeeded"] == 6
    assert summary["failed"] == 4
    assert len(summary["errors"]) == 4
    assert "Error 0" in summary["errors"]


def test_batch_tracker_progress_estimation():
    """Test progress tracking with time estimation."""
    tracker = BatchOperationTracker(total_items=100)

    # Simulate processing first 25 items
    for _ in range(25):
        tracker.record_success()

    # Allow some time to pass for calculation
    import time
    time.sleep(0.1)

    progress = tracker.get_progress()
    assert progress["percent_complete"] == 25.0
    assert progress["duration_seconds"] > 0
    # Estimated remaining should be calculated
    assert progress["estimated_remaining_seconds"] >= 0


def test_batch_tracker_zero_items():
    """Test batch tracker with zero items."""
    tracker = BatchOperationTracker(total_items=0)

    progress = tracker.get_progress()
    assert progress["total"] == 0
    assert progress["percent_complete"] == 100.0  # 100% of nothing is done!


def test_batch_tracker_summary():
    """Test final summary generation."""
    tracker = BatchOperationTracker(total_items=5)

    tracker.record_success()
    tracker.record_success()
    tracker.record_failure("Error 1")
    tracker.record_failure("Error 2")
    tracker.record_success()

    summary = tracker.get_summary()
    assert summary["total"] == 5
    assert summary["succeeded"] == 3
    assert summary["failed"] == 2
    assert len(summary["errors"]) == 2
    assert summary["duration_seconds"] >= 0  # May be 0 for fast tests
