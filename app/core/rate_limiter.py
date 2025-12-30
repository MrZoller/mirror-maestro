"""
Rate limiting utilities for GitLab API operations.

Provides configurable delays and retry logic to prevent overwhelming GitLab instances
with too many API requests in rapid succession.
"""

import asyncio
import logging
from typing import Callable, TypeVar, Any
from datetime import datetime

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RateLimiter:
    """
    Rate limiter for GitLab API operations.

    Provides:
    - Configurable delays between operations
    - Exponential backoff retry logic for rate limit errors
    - Tracking of operation counts and timing
    """

    def __init__(self, delay_ms: int = 200, max_retries: int = 3):
        """
        Initialize rate limiter.

        Args:
            delay_ms: Delay in milliseconds between operations
            max_retries: Maximum number of retries on rate limit errors
        """
        self.delay_ms = delay_ms
        self.max_retries = max_retries
        self.operation_count = 0
        self.start_time: datetime | None = None

    async def delay(self) -> None:
        """Apply configured delay between operations."""
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)

    def start_tracking(self) -> None:
        """Start tracking operations (for metrics/logging)."""
        self.operation_count = 0
        self.start_time = datetime.utcnow()

    def record_operation(self) -> None:
        """Record that an operation was performed."""
        self.operation_count += 1

    def get_metrics(self) -> dict[str, Any]:
        """
        Get metrics about rate-limited operations.

        Returns:
            Dictionary with operation count, duration, and rate
        """
        if self.start_time is None:
            return {
                "operation_count": self.operation_count,
                "duration_seconds": 0,
                "operations_per_second": 0
            }

        duration = (datetime.utcnow() - self.start_time).total_seconds()
        ops_per_sec = self.operation_count / duration if duration > 0 else 0

        return {
            "operation_count": self.operation_count,
            "duration_seconds": round(duration, 2),
            "operations_per_second": round(ops_per_sec, 2)
        }

    async def execute_with_retry(
        self,
        operation: Callable[[], T],
        operation_name: str = "operation"
    ) -> T:
        """
        Execute an operation with retry logic for rate limit errors.

        Args:
            operation: Callable that performs the operation
            operation_name: Name for logging purposes

        Returns:
            Result of the operation

        Raises:
            Exception: If all retries are exhausted
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                result = operation()
                self.record_operation()
                return result
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Check if this is a rate limit error (429 or "rate limit" in message)
                is_rate_limit = (
                    "429" in error_str or
                    "rate limit" in error_str or
                    "too many requests" in error_str
                )

                if is_rate_limit and attempt < self.max_retries:
                    # Exponential backoff: 2^attempt seconds
                    backoff_seconds = 2 ** attempt
                    logger.warning(
                        f"Rate limit hit for {operation_name} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {backoff_seconds}s..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue
                else:
                    # Not a rate limit error, or out of retries
                    raise

        # Should never reach here, but just in case
        raise last_error if last_error else Exception(f"Failed to execute {operation_name}")


class BatchOperationTracker:
    """
    Track progress of batch operations with rate limiting.

    Useful for operations that process many items sequentially,
    providing progress updates and error tracking.
    """

    def __init__(self, total_items: int):
        """
        Initialize batch tracker.

        Args:
            total_items: Total number of items to process
        """
        self.total_items = total_items
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.errors: list[str] = []
        self.start_time = datetime.utcnow()

    def record_success(self) -> None:
        """Record a successful operation."""
        self.processed += 1
        self.succeeded += 1

    def record_failure(self, error_msg: str) -> None:
        """Record a failed operation."""
        self.processed += 1
        self.failed += 1
        self.errors.append(error_msg)

    def get_progress(self) -> dict[str, Any]:
        """
        Get current progress metrics.

        Returns:
            Dictionary with progress information
        """
        duration = (datetime.utcnow() - self.start_time).total_seconds()
        percent_complete = (self.processed / self.total_items * 100) if self.total_items > 0 else 100

        # Estimate remaining time
        if self.processed > 0 and duration > 0:
            time_per_item = duration / self.processed
            remaining_items = self.total_items - self.processed
            estimated_remaining_seconds = time_per_item * remaining_items
        else:
            estimated_remaining_seconds = 0

        return {
            "total": self.total_items,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "percent_complete": round(percent_complete, 1),
            "duration_seconds": round(duration, 2),
            "estimated_remaining_seconds": round(estimated_remaining_seconds, 2)
        }

    def get_summary(self) -> dict[str, Any]:
        """
        Get final summary of batch operation.

        Returns:
            Dictionary with summary information
        """
        duration = (datetime.utcnow() - self.start_time).total_seconds()

        return {
            "total": self.total_items,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "errors": self.errors,
            "duration_seconds": round(duration, 2)
        }
