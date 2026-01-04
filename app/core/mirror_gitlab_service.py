"""
GitLab service wrapper for mirror operations with enterprise-grade robustness.

Provides rate limiting, retry logic with exponential backoff, and circuit breakers
for all mirror-related GitLab API operations. This aligns mirror management with
the robustness patterns used in issue syncing.
"""

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, TypeVar
from functools import wraps

from app.config import settings
from app.core.gitlab_client import (
    GitLabClient,
    GitLabClientError,
    GitLabRateLimitError,
    GitLabConnectionError,
    GitLabServerError,
)
from app.core.rate_limiter import RateLimiter, CircuitBreaker, BatchOperationTracker

logger = logging.getLogger(__name__)

T = TypeVar('T')


class MirrorGitLabService:
    """
    Service class that wraps GitLabClient with robustness patterns.

    Features:
    - Rate limiting between API calls (configurable delay)
    - Exponential backoff retry on rate limit errors
    - Circuit breaker per GitLab instance to prevent cascading failures
    - Batch operation tracking for bulk operations

    Usage:
        service = MirrorGitLabService()

        # Execute with all protections
        result = await service.execute(
            instance_url="https://gitlab.example.com",
            operation=lambda client: client.create_push_mirror(...),
            operation_name="create_push_mirror"
        )
    """

    def __init__(
        self,
        delay_ms: Optional[int] = None,
        max_retries: Optional[int] = None,
        circuit_breaker_threshold: Optional[int] = None,
        circuit_breaker_recovery: Optional[int] = None,
    ):
        """
        Initialize the mirror GitLab service.

        Args:
            delay_ms: Delay between operations in milliseconds (default: from settings)
            max_retries: Max retries on rate limit errors (default: from settings)
            circuit_breaker_threshold: Failures before circuit opens (default: from settings)
            circuit_breaker_recovery: Seconds before recovery attempt (default: from settings)
        """
        self.delay_ms = delay_ms if delay_ms is not None else settings.gitlab_api_delay_ms
        self.max_retries = max_retries if max_retries is not None else settings.gitlab_api_max_retries
        self.circuit_breaker_threshold = (
            circuit_breaker_threshold
            if circuit_breaker_threshold is not None
            else settings.circuit_breaker_failure_threshold
        )
        self.circuit_breaker_recovery = (
            circuit_breaker_recovery
            if circuit_breaker_recovery is not None
            else settings.circuit_breaker_recovery_timeout
        )

        # Rate limiter shared across all operations
        self.rate_limiter = RateLimiter(
            delay_ms=self.delay_ms,
            max_retries=self.max_retries,
        )

        # Circuit breakers per GitLab instance URL
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._circuit_breakers_lock = threading.Lock()

        # Track metrics
        self.rate_limiter.start_tracking()

    def _get_circuit_breaker(self, instance_url: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a GitLab instance (thread-safe)."""
        with self._circuit_breakers_lock:
            if instance_url not in self._circuit_breakers:
                self._circuit_breakers[instance_url] = CircuitBreaker(
                    failure_threshold=self.circuit_breaker_threshold,
                    recovery_timeout=self.circuit_breaker_recovery,
                    expected_exception=GitLabClientError,
                )
            return self._circuit_breakers[instance_url]

    async def execute(
        self,
        client: GitLabClient,
        operation: Callable[[GitLabClient], T],
        operation_name: str = "operation",
    ) -> T:
        """
        Execute a GitLab operation with all robustness protections.

        Args:
            client: The GitLabClient to use
            operation: A callable that takes the client and performs the operation
            operation_name: Name for logging purposes

        Returns:
            The result of the operation

        Raises:
            GitLabClientError: If all retries are exhausted or circuit is open
        """
        # Get circuit breaker for this instance
        circuit_breaker = self._get_circuit_breaker(client.url)

        # Check circuit state using thread-safe method
        state, is_available = circuit_breaker.check_and_transition()
        if not is_available:
            raise GitLabConnectionError(
                f"{operation_name}: Circuit breaker is OPEN for {client.url}. "
                f"Service unavailable. Will retry after {self.circuit_breaker_recovery}s cooldown."
            )

        # Apply rate limiting delay
        await self.rate_limiter.delay()

        # Execute with retry logic
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                # Execute the operation through the circuit breaker
                result = circuit_breaker.call(operation, client)
                self.rate_limiter.record_operation()
                return result

            except GitLabRateLimitError as e:
                last_error = e
                if attempt < self.max_retries:
                    # Exponential backoff: 2^attempt seconds
                    backoff_seconds = 2 ** attempt
                    logger.warning(
                        f"Rate limit hit for {operation_name} on {client.url} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {backoff_seconds}s..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue
                else:
                    logger.error(
                        f"Rate limit exceeded for {operation_name} on {client.url} "
                        f"after {self.max_retries + 1} attempts"
                    )
                    raise

            except (GitLabConnectionError, GitLabServerError) as e:
                last_error = e
                if attempt < self.max_retries:
                    # Exponential backoff for transient errors
                    backoff_seconds = 2 ** attempt
                    logger.warning(
                        f"Transient error for {operation_name} on {client.url} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"Retrying in {backoff_seconds}s..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue
                else:
                    logger.error(
                        f"Operation {operation_name} failed on {client.url} "
                        f"after {self.max_retries + 1} attempts: {e}"
                    )
                    raise

            except GitLabClientError:
                # Non-transient errors (auth, permission, not found) - don't retry
                raise

            except Exception as e:
                # Unexpected errors - wrap and don't retry
                logger.error(f"Unexpected error in {operation_name}: {e}")
                raise GitLabClientError(f"{operation_name}: Unexpected error - {e}")

        # Should never reach here, but just in case
        if last_error:
            raise last_error
        raise GitLabClientError(f"Failed to execute {operation_name}")

    async def execute_batch(
        self,
        operations: List[Dict[str, Any]],
        batch_size: int = 50,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
    ) -> BatchOperationTracker:
        """
        Execute multiple operations in batches with progress tracking.

        Args:
            operations: List of dicts with 'client', 'operation', 'operation_name'
            batch_size: Number of operations per batch
            on_progress: Callback(processed, succeeded, failed) called after each batch

        Returns:
            BatchOperationTracker with final results
        """
        tracker = BatchOperationTracker(len(operations))

        for i, op in enumerate(operations):
            try:
                await self.execute(
                    client=op['client'],
                    operation=op['operation'],
                    operation_name=op.get('operation_name', f'operation_{i}'),
                )
                tracker.record_success()
            except Exception as e:
                tracker.record_failure(str(e))

            # Progress callback after each batch
            if on_progress and (i + 1) % batch_size == 0:
                on_progress(tracker.processed, tracker.succeeded, tracker.failed)

        # Final progress callback
        if on_progress and tracker.processed % batch_size != 0:
            on_progress(tracker.processed, tracker.succeeded, tracker.failed)

        return tracker

    def get_metrics(self) -> Dict[str, Any]:
        """Get metrics about operations performed."""
        metrics = self.rate_limiter.get_metrics()
        metrics['circuit_breakers'] = {
            url: cb.get_state()
            for url, cb in self._circuit_breakers.items()
        }
        return metrics

    def get_circuit_breaker_state(self, instance_url: str) -> Dict[str, Any]:
        """Get the circuit breaker state for a specific instance."""
        if instance_url in self._circuit_breakers:
            return self._circuit_breakers[instance_url].get_state()
        return {"state": "CLOSED", "failure_count": 0}

    def reset_circuit_breaker(self, instance_url: str) -> bool:
        """Manually reset a circuit breaker to CLOSED state (thread-safe)."""
        with self._circuit_breakers_lock:
            if instance_url not in self._circuit_breakers:
                return False
            cb = self._circuit_breakers[instance_url]

        # Use the CircuitBreaker's public reset method for proper encapsulation
        cb.reset()
        logger.info(f"Circuit breaker for {instance_url} manually reset to CLOSED")
        return True


# Singleton instance for use across the application
_mirror_gitlab_service: Optional[MirrorGitLabService] = None
_mirror_gitlab_service_lock = threading.Lock()


def get_mirror_gitlab_service() -> MirrorGitLabService:
    """Get the singleton MirrorGitLabService instance (thread-safe)."""
    global _mirror_gitlab_service
    if _mirror_gitlab_service is None:
        with _mirror_gitlab_service_lock:
            # Double-check inside lock to prevent race condition
            if _mirror_gitlab_service is None:
                _mirror_gitlab_service = MirrorGitLabService()
    return _mirror_gitlab_service


def reset_mirror_gitlab_service() -> None:
    """Reset the singleton (useful for testing)."""
    global _mirror_gitlab_service
    _mirror_gitlab_service = None
