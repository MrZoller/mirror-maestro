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


class CircuitBreaker:
    """
    Circuit breaker pattern for GitLab API operations.

    Prevents repeated calls to failing services by opening the circuit
    after a threshold of consecutive failures. The circuit automatically
    attempts to close after a cooldown period.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests are blocked
    - HALF_OPEN: Testing if service recovered, limited requests allowed

    Features:
    - Gradual recovery: Requires multiple consecutive successes to fully close
    - Configurable thresholds for both failure and recovery
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 3,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            success_threshold: Number of consecutive successes needed to close circuit
            expected_exception: Exception type that triggers the circuit breaker
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.success_count = 0  # Track consecutive successes in HALF_OPEN state
        self.last_failure_time: datetime | None = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def call(self, func, *args, **kwargs):
        """
        Execute a function through the circuit breaker.

        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result if successful

        Raises:
            Exception: If circuit is OPEN or function fails
        """
        if self.state == "OPEN":
            # Check if recovery timeout has elapsed
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
                self.success_count = 0  # Reset success count for new recovery attempt
                logger.info("Circuit breaker entering HALF_OPEN state, testing recovery")
            else:
                raise Exception(
                    f"Circuit breaker is OPEN. Service unavailable. "
                    f"Will retry after {self.recovery_timeout}s cooldown."
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt circuit reset."""
        if self.last_failure_time is None:
            return True
        elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _on_success(self):
        """Handle successful operation."""
        self.failure_count = 0

        if self.state == "HALF_OPEN":
            self.success_count += 1
            logger.debug(
                f"Circuit breaker recovery progress: {self.success_count}/{self.success_threshold} successes"
            )

            # Only close circuit after reaching success threshold
            if self.success_count >= self.success_threshold:
                self.state = "CLOSED"
                self.success_count = 0
                logger.info(
                    f"Circuit breaker recovered after {self.success_threshold} consecutive successes, "
                    f"state is now CLOSED"
                )

    def _on_failure(self):
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == "HALF_OPEN":
            # Failed during recovery attempt, reopen circuit
            self.state = "OPEN"
            self.success_count = 0
            logger.warning(
                f"Circuit breaker recovery failed after {self.success_count} successes, "
                f"reopening circuit"
            )
        elif self.failure_count >= self.failure_threshold:
            # Too many failures, open circuit
            self.state = "OPEN"
            logger.error(
                f"Circuit breaker OPENED after {self.failure_count} consecutive failures. "
                f"Will attempt recovery in {self.recovery_timeout}s"
            )

    def get_state(self) -> dict[str, Any]:
        """Get current circuit breaker state."""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "success_threshold": self.success_threshold,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "recovery_timeout": self.recovery_timeout
        }


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
