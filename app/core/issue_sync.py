"""Core issue mirroring sync engine."""

import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Any
from urllib.parse import urlparse
import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.gitlab_client import GitLabClient, GitLabClientError
from app.core.rate_limiter import RateLimiter, CircuitBreaker
from app.models import (
    Mirror,
    MirrorIssueConfig,
    IssueMapping,
    CommentMapping,
    LabelMapping,
    AttachmentMapping,
    GitLabInstance,
    InstancePair,
)


logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

MIRROR_FROM_LABEL_PREFIX = "Mirrored-From"
MIRROR_FROM_LABEL_COLOR = "#0052CC"
MIRROR_FOOTER_SEPARATOR = "\n\n---\n\n"
MIRROR_FOOTER_MARKER = "<!-- MIRROR_MAESTRO_FOOTER -->"


# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------

def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def extract_footer(description: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    Extract main content and footer from issue description.

    Returns:
        Tuple of (main_content, footer) where footer is None if not present.
    """
    if not description:
        return "", None

    if MIRROR_FOOTER_MARKER in description:
        parts = description.split(MIRROR_FOOTER_MARKER, 1)
        return parts[0].rstrip(), parts[1] if len(parts) > 1 else None

    return description, None


def build_footer(
    source_instance_url: str,
    source_project_path: str,
    source_issue_iid: int,
    source_web_url: str,
    milestone: Optional[Dict[str, Any]],
    iteration: Optional[Dict[str, Any]],
    epic: Optional[Dict[str, Any]],
    assignees: List[Dict[str, Any]],
) -> str:
    """
    Build description footer with PM field information.

    Returns:
        Markdown footer content.
    """
    lines = [
        MIRROR_FOOTER_MARKER,
        "",
        "### 📋 Mirror Information",
        "",
        f"🔗 **Source**: [{source_project_path}#{source_issue_iid}]({source_web_url})",
        "",
    ]

    # Add PM fields if present
    pm_fields = []

    if milestone:
        milestone_title = milestone.get("title", "Unknown")
        lines.append(f"🎯 **Milestone**: {milestone_title}")
        pm_fields.append(f"Milestone::{milestone_title}")

    if iteration:
        iteration_title = iteration.get("title", "Unknown")
        lines.append(f"🔄 **Iteration**: {iteration_title}")
        pm_fields.append(f"Iteration::{iteration_title}")

    if epic:
        epic_title = epic.get("title", "Unknown")
        epic_iid = epic.get("iid", "?")
        lines.append(f"🏔️ **Epic**: &{epic_iid} {epic_title}")
        pm_fields.append(f"Epic::&{epic_iid}")

    if assignees:
        assignee_names = [a.get("name", a.get("username", "Unknown")) for a in assignees]
        lines.append(f"👤 **Assignees**: {', '.join(assignee_names)}")
        for assignee in assignees:
            username = assignee.get("username", "Unknown")
            pm_fields.append(f"Assignee::@{username}")

    return "\n".join(lines)


def convert_pm_fields_to_labels(
    milestone: Optional[Dict[str, Any]],
    iteration: Optional[Dict[str, Any]],
    epic: Optional[Dict[str, Any]],
    assignees: List[Dict[str, Any]],
) -> List[str]:
    """
    Convert PM fields (milestone/iteration/epic/assignees) to label names.

    Returns:
        List of label names to apply.
    """
    labels = []

    if milestone:
        milestone_title = milestone.get("title", "").strip()
        if milestone_title:
            labels.append(f"Milestone::{milestone_title}")

    if iteration:
        iteration_title = iteration.get("title", "").strip()
        if iteration_title:
            labels.append(f"Iteration::{iteration_title}")

    if epic:
        epic_iid = epic.get("iid")
        if epic_iid:
            labels.append(f"Epic::&{epic_iid}")

    for assignee in assignees:
        username = assignee.get("username", "").strip()
        if username:
            labels.append(f"Assignee::@{username}")

    return labels


def _extract_hostname(url: str) -> str:
    """Extract hostname (with non-standard port) from a URL for use in labels.

    Standard ports (80 for http, 443 for https) are omitted.
    Examples:
        https://gitlab.example.com       → gitlab.example.com
        https://gitlab.example.com:8443  → gitlab.example.com:8443
        http://10.0.0.1:8080/            → 10.0.0.1:8080
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or parsed.netloc or url
    scheme = parsed.scheme or "https"

    # parsed.port raises ValueError on malformed ports (e.g. ":abc")
    try:
        port = parsed.port
    except ValueError:
        port = None

    # Include port only if non-standard
    standard_ports = {"http": 80, "https": 443}
    if port and port != standard_ports.get(scheme):
        return f"{hostname}:{port}"
    return hostname


def get_mirror_from_label(instance_url: str) -> str:
    """Get the Mirrored-From label name for a GitLab instance.

    Uses the instance URL hostname as a globally unique identifier,
    ensuring labels are unambiguous across multiple Mirror Maestro
    deployments that share GitLab instances.

    Args:
        instance_url: The GitLab instance URL (e.g. https://gitlab.example.com)

    Returns:
        Label string like 'Mirrored-From::gitlab.example.com'
    """
    host = _extract_hostname(instance_url)
    return f"{MIRROR_FROM_LABEL_PREFIX}::{host}"


def extract_mirror_urls_from_description(description: Optional[str]) -> Set[str]:
    """
    Extract attachment URLs from description markdown.

    Returns:
        Set of URLs found in markdown image/link syntax.
    """
    if not description:
        return set()

    urls = set()

    # Match markdown images: ![alt](url)
    image_pattern = r'!\[.*?\]\((https?://[^\)]+)\)'
    urls.update(re.findall(image_pattern, description))

    # Match markdown links: [text](url)
    link_pattern = r'\[.*?\]\((https?://[^\)]+)\)'
    urls.update(re.findall(link_pattern, description))

    return urls


def _is_private_ip(ip_str: str) -> bool:
    """
    Check if an IP address is in a private/reserved range.

    Prevents SSRF attacks by blocking requests to internal networks.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        # Check for private, loopback, link-local, reserved, and multicast
        return (
            ip.is_private or
            ip.is_loopback or
            ip.is_link_local or
            ip.is_reserved or
            ip.is_multicast or
            # Cloud metadata endpoints
            ip_str.startswith("169.254.") or  # AWS/Azure/GCP metadata
            ip_str == "100.100.100.200"  # Alibaba Cloud metadata
        )
    except ValueError:
        # Invalid IP - treat as potentially dangerous
        return True


async def _validate_url_for_ssrf(url: str) -> None:
    """
    Validate a URL to prevent SSRF attacks.

    Uses async DNS resolution to avoid blocking the event loop.

    Args:
        url: URL to validate.

    Raises:
        ValueError: If URL is potentially dangerous (private IP, bad scheme, etc.)
    """
    parsed = urlparse(url)

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http/https allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    # Block obviously dangerous hostnames
    dangerous_hostnames = {
        "localhost", "127.0.0.1", "::1", "0.0.0.0",
        "metadata.google.internal",  # GCP metadata
        "169.254.169.254",  # AWS/Azure/GCP metadata IP
    }
    if hostname.lower() in dangerous_hostnames:
        raise ValueError(f"Hostname '{hostname}' is not allowed")

    # Resolve hostname asynchronously and check if it points to a private IP
    try:
        # Use async DNS resolution to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if _is_private_ip(ip_str):
                raise ValueError(
                    f"Hostname '{hostname}' resolves to private IP '{ip_str}'. "
                    "Requests to internal networks are not allowed."
                )
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve hostname '{hostname}': {e}")


def _parse_content_length(header_value: Optional[str]) -> Optional[int]:
    """
    Safely parse Content-Length header value.

    Args:
        header_value: Raw header value (may be None or invalid).

    Returns:
        Parsed integer or None if invalid/missing.
    """
    if not header_value:
        return None
    try:
        size = int(header_value)
        # Reject negative or unreasonably large values (10GB limit)
        if size < 0 or size > 10 * 1024 * 1024 * 1024:
            logger.warning(f"Content-Length value out of range: {size}")
            return None
        return size
    except (ValueError, TypeError):
        logger.warning(f"Invalid Content-Length header: {header_value}")
        return None


async def download_file(url: str, max_retries: int = 3, max_size_bytes: int = 0) -> Optional[bytes]:
    """
    Download a file from a URL with retry logic, size limits, and SSRF protection.

    Args:
        url: URL to download from (must be http/https, not pointing to private IPs).
        max_retries: Maximum number of retry attempts (default: 3).
        max_size_bytes: Maximum file size in bytes (0 = unlimited).

    Returns:
        File content as bytes, or None if download failed after all retries.

    Raises:
        ValueError: If URL is invalid/dangerous or file size exceeds max_size_bytes.
    """
    # SSRF protection: validate URL before making request
    await _validate_url_for_ssrf(url)

    for attempt in range(max_retries):
        try:
            # Use manual redirect handling to validate each redirect URL for SSRF
            # Configure connection pool limits to prevent resource exhaustion
            limits = httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            )
            timeout = httpx.Timeout(
                timeout=float(settings.attachment_download_timeout),
                connect=10.0,  # Connection timeout
            )
            async with httpx.AsyncClient(
                follow_redirects=False,
                limits=limits,
                timeout=timeout,
            ) as client:
                current_url = url
                max_redirects = 10
                redirect_count = 0

                while True:
                    response = await client.get(current_url)

                    # Handle redirects manually to validate each redirect URL
                    if response.is_redirect and redirect_count < max_redirects:
                        redirect_url = response.headers.get("location")
                        if not redirect_url:
                            raise ValueError("Redirect response missing Location header")

                        # Make redirect URL absolute if relative
                        if redirect_url.startswith("/"):
                            from urllib.parse import urlparse, urlunparse
                            parsed = urlparse(current_url)
                            redirect_url = urlunparse((parsed.scheme, parsed.netloc, redirect_url, "", "", ""))
                        elif not redirect_url.startswith(("http://", "https://")):
                            raise ValueError(f"Invalid redirect URL: {redirect_url}")

                        # SSRF validation on redirect URL (prevents SSRF bypass via redirect)
                        await _validate_url_for_ssrf(redirect_url)

                        current_url = redirect_url
                        redirect_count += 1
                        continue

                    if response.is_redirect and redirect_count >= max_redirects:
                        raise ValueError(f"Too many redirects (max: {max_redirects})")

                    break

                response.raise_for_status()

                # Safely parse content length
                content_length = _parse_content_length(response.headers.get('content-length'))
                if content_length is not None and max_size_bytes > 0:
                    if content_length > max_size_bytes:
                        size_mb = content_length / (1024 * 1024)
                        max_mb = max_size_bytes / (1024 * 1024)
                        raise ValueError(
                            f"File size ({size_mb:.2f}MB) exceeds maximum allowed size ({max_mb:.2f}MB)"
                        )

                content = response.content

                # Double-check actual size
                if max_size_bytes > 0 and len(content) > max_size_bytes:
                    size_mb = len(content) / (1024 * 1024)
                    max_mb = max_size_bytes / (1024 * 1024)
                    raise ValueError(
                        f"File size ({size_mb:.2f}MB) exceeds maximum allowed size ({max_mb:.2f}MB)"
                    )

                return content
        except ValueError:
            # Don't retry validation/size limit errors
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                # Calculate exponential backoff delay: 1s, 2s, 4s
                delay = 2 ** attempt
                logger.warning(
                    f"Failed to download file from {url} (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Failed to download file from {url} after {max_retries} attempts: {e}")
                return None


def extract_filename_from_url(url: str) -> str:
    """Extract filename from URL."""
    path = urlparse(url).path
    filename = path.split('/')[-1] if '/' in path else ''
    return filename if filename else 'attachment'


def replace_urls_in_description(description: str, url_mapping: Dict[str, str]) -> str:
    """
    Replace source URLs with target URLs in description.

    Args:
        description: Original description with source URLs.
        url_mapping: Dict mapping source URLs to target URLs.

    Returns:
        Description with replaced URLs.
    """
    result = description
    for source_url, target_url in url_mapping.items():
        result = result.replace(source_url, target_url)
    return result


# -------------------------------------------------------------------------
# Issue Sync Engine
# -------------------------------------------------------------------------

class IssueSyncEngine:
    """Engine for syncing issues from source to target GitLab instance."""

    def __init__(
        self,
        db: AsyncSession,
        config: MirrorIssueConfig,
        mirror: Mirror,
        source_instance: GitLabInstance,
        target_instance: GitLabInstance,
        instance_pair: InstancePair,
    ):
        """Initialize sync engine."""
        self.db = db
        self.config = config
        self.mirror = mirror
        self.source_instance = source_instance
        self.target_instance = target_instance
        self.instance_pair = instance_pair

        # Issue sync always flows source → target, matching the mirror direction.
        # mirror.source_project lives on pair.source_instance and
        # mirror.target_project lives on pair.target_instance regardless of
        # whether the mirror is push or pull.
        self.source_project_id = mirror.source_project_id
        self.target_project_id = mirror.target_project_id
        self.source_project_path = mirror.source_project_path
        self.target_project_path = mirror.target_project_path

        # Initialize GitLab clients with timeout
        self.source_client = GitLabClient(
            source_instance.url,
            source_instance.encrypted_token,
            timeout=settings.gitlab_api_timeout
        )
        self.target_client = GitLabClient(
            target_instance.url,
            target_instance.encrypted_token,
            timeout=settings.gitlab_api_timeout
        )

        # Initialize rate limiter for GitLab API calls
        self.rate_limiter = RateLimiter(
            delay_ms=settings.gitlab_api_delay_ms,
            max_retries=settings.gitlab_api_max_retries
        )

        # Initialize circuit breakers for source and target GitLab instances
        # Uses configurable thresholds from settings
        self.source_circuit_breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
            expected_exception=GitLabClientError
        )
        self.target_circuit_breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
            expected_exception=GitLabClientError
        )

        # Cache for labels
        self.target_labels_cache: Dict[str, Dict[str, Any]] = {}
        self.mirror_from_label: str = get_mirror_from_label(source_instance.url)

        # Loop prevention: label that indicates issue originated from target instance
        # If a source issue has this label, it was mirrored FROM the target, so skip it
        self.originated_from_target_label: str = get_mirror_from_label(target_instance.url)

        # Backward compatibility: also recognise the legacy label format
        # (Mirrored-From::instance-{id}) used before URL-based identifiers.
        # Without this, issues tagged with the old format after an upgrade
        # would not be detected by loop prevention, risking duplicates.
        #
        # NOTE: The legacy check uses local DB IDs which can collide across
        # separate MM deployments — the same limitation that existed before
        # URL-based labels.  This is an accepted tradeoff: the legacy check
        # prevents duplicates during the single-instance upgrade transition,
        # while the new URL-based labels handle the multi-instance case.
        # Once issues are re-synced with new labels the legacy path becomes
        # a no-op.
        self._originated_from_target_label_legacy: str = (
            f"{MIRROR_FROM_LABEL_PREFIX}::instance-{target_instance.id}"
        )
        self._mirror_from_label_legacy: str = (
            f"{MIRROR_FROM_LABEL_PREFIX}::instance-{source_instance.id}"
        )

    async def _execute_gitlab_api_call(
        self,
        operation_func,
        operation_name: str,
        *args,
        **kwargs
    ):
        """
        Execute a GitLab API call with retry logic, rate limiting, and circuit breaker.

        Args:
            operation_func: The GitLab client method to call
            operation_name: Name of the operation for logging
            *args, **kwargs: Arguments to pass to the operation

        Returns:
            The result from the GitLab API call
        """
        # Determine which circuit breaker to use based on which client is being called
        if operation_func.__self__ == self.source_client:
            circuit_breaker = self.source_circuit_breaker
        elif operation_func.__self__ == self.target_client:
            circuit_breaker = self.target_circuit_breaker
        else:
            # Unknown client, skip circuit breaker
            circuit_breaker = None

        async def _execute():
            # Apply rate limiting delay
            await self.rate_limiter.delay()

            # Define the operation
            def operation():
                return operation_func(*args, **kwargs)

            # Execute the operation with retry logic and circuit breaker
            if circuit_breaker:
                # Thread-safe check of circuit breaker state with automatic HALF_OPEN transition
                current_state, is_available = circuit_breaker.check_and_transition()
                if not is_available:
                    raise Exception(
                        f"Circuit breaker is OPEN. Service unavailable. "
                        f"Will retry after {circuit_breaker.recovery_timeout}s cooldown."
                    )

                try:
                    # Execute with retry logic (async)
                    result = await self.rate_limiter.execute_with_retry(
                        operation,
                        operation_name=operation_name
                    )
                    # Mark success on circuit breaker (thread-safe)
                    with circuit_breaker._lock:
                        circuit_breaker._on_success()
                    return result
                except Exception as e:
                    # Mark failure on circuit breaker (thread-safe)
                    with circuit_breaker._lock:
                        circuit_breaker._on_failure()
                    raise
            else:
                # No circuit breaker, just use retry logic
                return await self.rate_limiter.execute_with_retry(
                    operation,
                    operation_name=operation_name
                )

        return await _execute()

    async def sync(self) -> Dict[str, Any]:
        """
        Perform a full sync of issues.

        Returns:
            Dict with sync statistics.
        """
        logger.info(
            f"Starting issue sync for mirror {self.mirror.id} "
            f"({self.source_project_path} → {self.target_project_path})"
        )

        stats = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": [],
        }

        try:
            # Load target labels into cache
            await self._load_target_labels_cache()

            # Ensure Mirrored-From label exists on target
            await self._ensure_mirror_from_label()

            # Determine which issues to sync
            if self.config.last_sync_at and not self.config.sync_existing_issues:
                # Incremental sync: only issues updated after last sync
                updated_after = self.config.last_sync_at.isoformat() + "Z"
                logger.info(f"Incremental sync: fetching issues updated after {updated_after}")
            elif not self.config.sync_existing_issues:
                # First sync without sync_existing_issues: use current time as baseline
                # This means we won't sync any existing issues, only new ones going forward
                updated_after = datetime.utcnow().isoformat() + "Z"
                logger.info("First sync without sync_existing_issues: only new issues will be synced")
            else:
                # Sync all issues (sync_existing_issues = True)
                updated_after = None
                logger.info("Full sync: fetching all issues")

            # Determine state filter
            state_filter = "all" if self.config.sync_closed_issues else "opened"

            # Fetch issues from source with configurable pagination limit
            # This prevents memory issues and excessive API calls
            max_pages = settings.max_pages_per_request
            source_issues = await self._execute_gitlab_api_call(
                self.source_client.get_issues,
                "fetch_source_issues",
                self.source_project_id,
                updated_after=updated_after,
                state=state_filter,
                per_page=100,
                get_all=True,
                max_pages=max_pages,
            )

            logger.info(f"Found {len(source_issues)} issues to process")

            # Process issues in batches with progress checkpointing
            batch_size = settings.issue_batch_size
            total_issues = len(source_issues)

            for batch_start in range(0, total_issues, batch_size):
                batch_end = min(batch_start + batch_size, total_issues)
                batch = source_issues[batch_start:batch_end]

                logger.info(f"Processing batch {batch_start // batch_size + 1}: issues {batch_start + 1}-{batch_end} of {total_issues}")

                # Process each issue in the batch
                for idx, source_issue in enumerate(batch):
                    try:
                        await self._sync_issue(source_issue, stats)

                        # Apply rate limiting delay between issues (except after last one)
                        if idx < len(batch) - 1:
                            await self.rate_limiter.delay()

                    except Exception as e:
                        logger.error(
                            f"Failed to sync issue {source_issue.get('iid')}: {e}",
                            exc_info=True
                        )
                        stats["issues_failed"] += 1
                        stats["errors"].append({
                            "issue_iid": source_issue.get("iid"),
                            "error": str(e)
                        })

                # Checkpoint: Update config with progress timestamp only
                # Note: Do NOT set last_sync_status here - only the final outcome should set it
                # Setting "in_progress" here caused status to get stuck if post-sync commit failed
                try:
                    self.config.last_sync_at = datetime.utcnow()
                    await self.db.commit()
                    logger.debug(f"Progress checkpoint saved: {batch_end}/{total_issues} issues processed")
                except Exception as checkpoint_error:
                    logger.warning(f"Failed to save progress checkpoint: {checkpoint_error}")
                    # Continue processing - checkpoint failure is not critical

            # Set final sync status before returning to caller
            # This ensures status is correct even if caller's commit fails
            if stats["issues_failed"] > 0 and stats["issues_created"] + stats["issues_updated"] > 0:
                self.config.last_sync_status = "partial"
            elif stats["issues_failed"] > 0:
                self.config.last_sync_status = "failed"
            else:
                self.config.last_sync_status = "success"

            self.config.last_sync_error = None
            await self.db.commit()

            logger.info(
                f"Issue sync completed: {stats['issues_created']} created, "
                f"{stats['issues_updated']} updated, {stats['issues_skipped']} skipped, "
                f"{stats['issues_failed']} failed"
            )

            return stats

        except Exception as e:
            logger.error(f"Issue sync failed: {e}", exc_info=True)
            stats["errors"].append({"error": str(e)})
            raise

    async def _sync_issue(
        self,
        source_issue: Dict[str, Any],
        stats: Dict[str, Any]
    ) -> None:
        """Sync a single issue from source to target."""
        source_issue_id = source_issue.get("id")
        source_issue_iid = source_issue.get("iid")

        # Validate required fields from GitLab API response
        if source_issue_id is None or source_issue_iid is None:
            logger.error(
                f"Invalid issue from GitLab API: missing 'id' or 'iid'. "
                f"Keys present: {list(source_issue.keys())}"
            )
            stats["issues_failed"] += 1
            return

        stats["issues_processed"] += 1

        # Loop prevention: Skip issues that originated from the target instance
        # These have a Mirrored-From label pointing to the target, meaning they were
        # created by the reverse sync and shouldn't be synced back (would create duplicates)
        # Also check the legacy label format for backward compatibility after upgrades.
        source_labels = source_issue.get("labels", [])
        if (self.originated_from_target_label in source_labels
                or self._originated_from_target_label_legacy in source_labels):
            logger.debug(
                f"Skipping issue {source_issue_iid}: originated from target instance "
                f"(has label '{self.originated_from_target_label}')"
            )
            stats["issues_skipped"] += 1
            return

        # Check if issue is already mirrored
        result = await self.db.execute(
            select(IssueMapping).where(
                IssueMapping.mirror_issue_config_id == self.config.id,
                IssueMapping.source_issue_id == source_issue_id,
            )
        )
        mapping = result.scalar_one_or_none()

        # Compute content hash for change detection
        source_title = source_issue.get('title', '')
        source_description = source_issue.get('description', '')
        content_to_hash = f"{source_title}|||{source_description}"
        current_hash = compute_content_hash(content_to_hash)

        if mapping:
            # Issue already mirrored - check if update needed
            if not self.config.update_existing:
                logger.debug(f"Skipping issue {source_issue_iid}: update_existing is False")
                stats["issues_skipped"] += 1
                return

            if mapping.source_content_hash == current_hash:
                logger.debug(f"Skipping issue {source_issue_iid}: no changes detected")
                stats["issues_skipped"] += 1
                return

            # Update existing issue
            await self._update_target_issue(source_issue, mapping, current_hash)
            stats["issues_updated"] += 1
        else:
            # Create new issue
            await self._create_target_issue(source_issue, current_hash)
            stats["issues_created"] += 1

    async def _create_target_issue(
        self,
        source_issue: Dict[str, Any],
        content_hash: str
    ) -> None:
        """
        Create a new issue on target instance with idempotency protection.

        Checks if the issue already exists on target (orphaned from previous failed sync)
        before creating a new one.
        """
        # Validate required fields from source issue
        source_issue_id = source_issue.get("id")
        source_issue_iid = source_issue.get("iid")
        source_issue_title = source_issue.get("title", "Untitled Issue")
        source_issue_state = source_issue.get("state", "opened")

        if source_issue_id is None or source_issue_iid is None:
            raise ValueError(f"Invalid source issue: missing 'id' or 'iid' field")

        # Idempotency check: search for existing issue with same source reference
        # This prevents creating duplicates if DB commit failed but GitLab creation succeeded
        existing_target_issue = await self._find_existing_target_issue(source_issue_id, source_issue_iid)
        if existing_target_issue:
            target_issue_id = existing_target_issue.get("id")
            target_issue_iid = existing_target_issue.get("iid")
            if target_issue_id is None or target_issue_iid is None:
                raise ValueError(f"Invalid existing target issue: missing 'id' or 'iid' field")
            logger.info(
                f"Found orphaned issue {target_issue_iid} for source issue {source_issue_iid}. "
                "Reusing and updating it with current source content."
            )

            # Update the orphaned issue with current source content
            # to ensure it's in sync before marking the mapping as synced
            labels = self._prepare_labels(source_issue)
            description = self._prepare_description(source_issue)

            # Handle attachments if enabled
            if self.config.sync_attachments:
                description = await self._sync_attachments_in_description(
                    description,
                    source_issue_id,
                    None,  # No existing mapping yet
                )

            # Update the target issue
            target_issue = await self._execute_gitlab_api_call(
                self.target_client.update_issue,
                f"update_orphaned_issue_{target_issue_iid}",
                self.target_project_id,
                target_issue_iid,
                title=source_issue_title,
                description=description,
                labels=labels,
                weight=source_issue.get("weight") if self.config.sync_weight else None,
            )

            logger.info(
                f"Updated orphaned issue {target_issue_iid} with current source content"
            )
        else:
            # Prepare labels
            labels = self._prepare_labels(source_issue)

            # Prepare description with footer
            description = self._prepare_description(source_issue)

            # Handle attachments if enabled
            if self.config.sync_attachments:
                description = await self._sync_attachments_in_description(
                    description,
                    source_issue_id,
                    None,  # No existing mapping yet
                )

            # Create issue on target with retry logic
            target_issue = await self._execute_gitlab_api_call(
                self.target_client.create_issue,
                f"create_issue_{source_issue_iid}",
                self.target_project_id,
                title=source_issue_title,
                description=description,
                labels=labels,
                weight=source_issue.get("weight") if self.config.sync_weight else None,
            )

            target_issue_id = target_issue.get("id")
            target_issue_iid = target_issue.get("iid")
            if target_issue_id is None or target_issue_iid is None:
                raise ValueError(f"GitLab create_issue returned invalid response: missing 'id' or 'iid'")

            logger.info(
                f"Created target issue {target_issue_iid} for source issue {source_issue_iid}"
            )

        # Sync time tracking if enabled
        if self.config.sync_time_estimate or self.config.sync_time_spent:
            await self._sync_time_tracking(source_issue, target_issue_iid)

        # Close target issue if source is closed
        if source_issue_state == "closed" and self.config.sync_closed_issues:
            await self._execute_gitlab_api_call(
                self.target_client.update_issue,
                f"close_issue_{target_issue_iid}",
                self.target_project_id,
                target_issue_iid,
                state_event="close"
            )

        # Create issue mapping with initial status "pending" until all operations complete
        # This prevents incorrect "synced" status if post-creation operations fail
        mapping = IssueMapping(
            mirror_issue_config_id=self.config.id,
            source_issue_id=source_issue_id,
            source_issue_iid=source_issue_iid,
            source_project_id=self.source_project_id,
            target_issue_id=target_issue_id,
            target_issue_iid=target_issue_iid,
            target_project_id=self.target_project_id,
            last_synced_at=datetime.utcnow(),
            source_updated_at=self._parse_datetime(source_issue.get("updated_at")),
            target_updated_at=datetime.utcnow(),
            sync_status="pending",  # Start as pending, update to synced only on full success
            source_content_hash=content_hash,
        )
        self.db.add(mapping)

        try:
            # Commit the mapping first to get the ID for comment/attachment mappings
            await self.db.commit()
            await self.db.refresh(mapping)

            # Sync comments if enabled (uses mapping.id for foreign key)
            if self.config.sync_comments:
                await self._sync_comments(source_issue_iid, target_issue_iid, mapping.id)

            # All operations succeeded - mark as fully synced
            mapping.sync_status = "synced"
            await self.db.commit()

        except Exception as e:
            # If post-creation sync fails, log warning but don't fail entire sync
            # The issue is created on GitLab and mapped in DB - comments can sync later
            logger.warning(
                f"Issue {target_issue_iid} created successfully, but post-creation sync failed: {e}"
            )
            # Update mapping status to indicate partial sync
            mapping.sync_status = "partial"
            try:
                await self.db.commit()
            except Exception as commit_error:
                logger.error(f"Failed to update sync status: {commit_error}")
                await self.db.rollback()

    async def _update_target_issue(
        self,
        source_issue: Dict[str, Any],
        mapping: IssueMapping,
        content_hash: str
    ) -> None:
        """Update an existing issue on target instance."""
        # Validate required fields from source issue
        source_issue_id = source_issue.get("id")
        source_issue_iid = source_issue.get("iid")
        source_issue_title = source_issue.get("title", "Untitled Issue")
        source_issue_state = source_issue.get("state", "opened")

        if source_issue_id is None or source_issue_iid is None:
            raise ValueError(f"Invalid source issue: missing 'id' or 'iid' field")

        target_issue_iid = mapping.target_issue_iid

        # Prepare labels
        labels = self._prepare_labels(source_issue)

        # Prepare description with footer
        description = self._prepare_description(source_issue)

        # Handle attachments if enabled
        if self.config.sync_attachments:
            description = await self._sync_attachments_in_description(
                description,
                source_issue_id,
                mapping.id,
            )

        # Update issue on target with retry logic
        await self._execute_gitlab_api_call(
            self.target_client.update_issue,
            f"update_issue_{target_issue_iid}",
            self.target_project_id,
            target_issue_iid,
            title=source_issue_title,
            description=description,
            labels=labels,
            weight=source_issue.get("weight") if self.config.sync_weight else None,
        )

        logger.info(
            f"Updated target issue {target_issue_iid} for source issue {source_issue_iid}"
        )

        # Sync time tracking if enabled
        if self.config.sync_time_estimate or self.config.sync_time_spent:
            await self._sync_time_tracking(source_issue, target_issue_iid)

        # Sync state (open/closed) with retry logic
        if source_issue_state == "closed" and self.config.sync_closed_issues:
            await self._execute_gitlab_api_call(
                self.target_client.update_issue,
                f"close_issue_{target_issue_iid}",
                self.target_project_id,
                target_issue_iid,
                state_event="close"
            )
        elif source_issue_state == "opened":
            await self._execute_gitlab_api_call(
                self.target_client.update_issue,
                f"reopen_issue_{target_issue_iid}",
                self.target_project_id,
                target_issue_iid,
                state_event="reopen"
            )

        # Update mapping - initially set to pending until post-update operations complete
        mapping.source_content_hash = content_hash
        mapping.last_synced_at = datetime.utcnow()
        mapping.source_updated_at = self._parse_datetime(source_issue.get("updated_at"))
        mapping.target_updated_at = datetime.utcnow()
        mapping.sync_status = "pending"  # Will be updated to "synced" on full success

        try:
            await self.db.commit()

            # Sync comments if enabled
            if self.config.sync_comments:
                await self._sync_comments(source_issue_iid, target_issue_iid, mapping.id)

            # All operations succeeded - mark as fully synced
            mapping.sync_status = "synced"
            await self.db.commit()

        except Exception as e:
            # If post-update sync fails, log warning but don't fail entire sync
            logger.warning(
                f"Issue {target_issue_iid} updated successfully, but post-update sync failed: {e}"
            )
            # Update mapping status to indicate partial sync
            mapping.sync_status = "partial"
            try:
                await self.db.commit()
            except Exception as commit_error:
                logger.error(f"Failed to update sync status: {commit_error}")
                await self.db.rollback()

    def _prepare_labels(self, source_issue: Dict[str, Any]) -> List[str]:
        """Prepare labels for target issue, including PM field conversions."""
        labels = []

        # Add Mirrored-From label
        labels.append(self.mirror_from_label)

        # Add source labels if enabled.
        # Upstream Mirrored-From:: labels are intentionally preserved so that
        # cyclic topologies (A→B→C→A) can detect that an issue has already
        # visited the target instance and skip it during loop prevention.
        if self.config.sync_labels:
            source_labels = source_issue.get("labels", [])
            labels.extend(source_labels)

        # Convert PM fields to labels
        pm_labels = convert_pm_fields_to_labels(
            milestone=source_issue.get("milestone"),
            iteration=source_issue.get("iteration"),  # May not be present in all GitLab versions
            epic=source_issue.get("epic"),
            assignees=source_issue.get("assignees", []),
        )
        labels.extend(pm_labels)

        return labels

    def _prepare_description(self, source_issue: Dict[str, Any]) -> str:
        """Prepare description with footer containing PM field information."""
        original_description = source_issue.get("description") or ""

        # Extract main content (remove old footer if present)
        main_content, _ = extract_footer(original_description)

        # Build new footer
        source_issue_iid = source_issue.get("iid")
        source_web_url = source_issue.get("web_url", "")
        if source_issue_iid is None:
            raise ValueError("Cannot prepare description: source issue missing 'iid' field")

        footer = build_footer(
            source_instance_url=self.source_instance.url,
            source_project_path=self.source_project_path,
            source_issue_iid=source_issue_iid,
            source_web_url=source_web_url,
            milestone=source_issue.get("milestone"),
            iteration=source_issue.get("iteration"),
            epic=source_issue.get("epic"),
            assignees=source_issue.get("assignees", []),
        )

        # Combine main content and footer
        return f"{main_content}{MIRROR_FOOTER_SEPARATOR}{footer}"

    async def _sync_time_tracking(
        self,
        source_issue: Dict[str, Any],
        target_issue_iid: int
    ) -> None:
        """Sync time estimate and time spent from source to target issue."""
        time_stats = source_issue.get("time_stats")
        if not time_stats:
            return

        # Sync time estimate with retry logic
        if self.config.sync_time_estimate:
            time_estimate = time_stats.get("time_estimate")
            if time_estimate and time_estimate > 0:
                # Convert seconds to human-readable format
                duration = self._seconds_to_duration(time_estimate)
                await self._execute_gitlab_api_call(
                    self.target_client.set_time_estimate,
                    f"set_time_estimate_{target_issue_iid}",
                    self.target_project_id,
                    target_issue_iid,
                    duration
                )

        # Sync time spent with retry logic
        if self.config.sync_time_spent:
            total_time_spent = time_stats.get("total_time_spent")
            if total_time_spent and total_time_spent > 0:
                # Reset time spent first, then add
                await self._execute_gitlab_api_call(
                    self.target_client.reset_time_spent,
                    f"reset_time_spent_{target_issue_iid}",
                    self.target_project_id,
                    target_issue_iid
                )
                duration = self._seconds_to_duration(total_time_spent)
                await self._execute_gitlab_api_call(
                    self.target_client.add_time_spent,
                    f"add_time_spent_{target_issue_iid}",
                    self.target_project_id,
                    target_issue_iid,
                    duration
                )

    async def _sync_comments(
        self,
        source_issue_iid: int,
        target_issue_iid: int,
        issue_mapping_id: int
    ) -> None:
        """
        Sync comments from source to target issue with batched commits.

        Uses batched database commits for better transaction safety and performance.
        """
        # Fetch source comments (excluding system notes) with retry logic
        source_notes = await self._execute_gitlab_api_call(
            self.source_client.get_issue_notes,
            f"get_issue_notes_{source_issue_iid}",
            self.source_project_id,
            source_issue_iid
        )

        source_notes = [n for n in source_notes if not n.get("system", False)]

        # Fetch existing comment mappings
        result = await self.db.execute(
            select(CommentMapping).where(
                CommentMapping.issue_mapping_id == issue_mapping_id
            )
        )
        existing_mappings = {m.source_note_id: m for m in result.scalars().all()}

        # Track changes to commit in batch
        mappings_to_add = []
        mappings_to_update = []

        for source_note in source_notes:
            source_note_id = source_note.get("id")
            if source_note_id is None:
                logger.warning(
                    f"Skipping note with missing 'id'. Keys present: {list(source_note.keys())}"
                )
                continue

            source_note_body = source_note.get("body", "")
            content_hash = compute_content_hash(source_note_body)

            if source_note_id in existing_mappings:
                # Comment already synced - check if update needed
                mapping = existing_mappings[source_note_id]

                if mapping.source_content_hash == content_hash:
                    # No changes
                    continue

                # Update target comment with retry logic
                await self._execute_gitlab_api_call(
                    self.target_client.update_issue_note,
                    f"update_note_{mapping.target_note_id}",
                    self.target_project_id,
                    target_issue_iid,
                    mapping.target_note_id,
                    source_note_body
                )

                mapping.source_content_hash = content_hash
                mapping.last_synced_at = datetime.utcnow()
                mappings_to_update.append(mapping)
            else:
                # Create new comment on target with retry logic
                target_note = await self._execute_gitlab_api_call(
                    self.target_client.create_issue_note,
                    f"create_note_{source_note_id}",
                    self.target_project_id,
                    target_issue_iid,
                    source_note_body
                )

                # Validate response from GitLab API
                target_note_id = target_note.get("id") if isinstance(target_note, dict) else None
                if target_note_id is None:
                    logger.error(
                        f"Failed to create comment: GitLab API returned invalid response. "
                        f"Response type: {type(target_note).__name__}"
                    )
                    continue

                # Create mapping (will be added to DB in batch)
                mapping = CommentMapping(
                    issue_mapping_id=issue_mapping_id,
                    source_note_id=source_note_id,
                    target_note_id=target_note_id,
                    last_synced_at=datetime.utcnow(),
                    source_content_hash=content_hash,
                )
                mappings_to_add.append(mapping)

        # Batch commit all changes for better transaction safety
        try:
            for mapping in mappings_to_add:
                self.db.add(mapping)

            if mappings_to_add or mappings_to_update:
                await self.db.commit()
                logger.debug(
                    f"Synced {len(mappings_to_add)} new comments, "
                    f"updated {len(mappings_to_update)} comments"
                )
        except Exception as e:
            logger.error(f"Failed to commit comment mappings: {e}")
            await self.db.rollback()
            raise

    async def _sync_attachments_in_description(
        self,
        description: str,
        source_issue_id: int,
        issue_mapping_id: Optional[int]
    ) -> str:
        """
        Sync attachments referenced in description with batched commits.

        Downloads files from source URLs and uploads to target,
        then replaces URLs in description.

        Returns:
            Description with replaced URLs.
        """
        # Extract URLs from description
        source_urls = extract_mirror_urls_from_description(description)

        if not source_urls:
            return description

        url_mapping = {}
        attachment_mappings_to_add = []

        for source_url in source_urls:
            # Check if this attachment was already synced
            if issue_mapping_id:
                result = await self.db.execute(
                    select(AttachmentMapping).where(
                        AttachmentMapping.issue_mapping_id == issue_mapping_id,
                        AttachmentMapping.source_url == source_url,
                    )
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # Already synced - reuse target URL
                    url_mapping[source_url] = existing.target_url
                    continue

            # Download file from source with size limit
            max_size_bytes = settings.max_attachment_size_mb * 1024 * 1024 if settings.max_attachment_size_mb > 0 else 0
            try:
                file_content = await download_file(source_url, max_size_bytes=max_size_bytes)
                if not file_content:
                    logger.warning(f"Failed to download attachment: {source_url}")
                    continue
            except ValueError as e:
                logger.warning(f"Skipping attachment due to size limit: {source_url} - {e}")
                continue

            # Extract filename
            filename = extract_filename_from_url(source_url)

            # Upload to target with retry logic
            try:
                upload_result = await self._execute_gitlab_api_call(
                    self.target_client.upload_file,
                    f"upload_file_{filename}",
                    self.target_project_id,
                    file_content,
                    filename
                )

                # Get full URL (GitLab returns relative URL)
                target_url = upload_result.get("url")
                if not target_url:
                    logger.warning(f"Upload succeeded but no URL returned for attachment {filename}, skipping URL replacement")
                    continue

                if not target_url.startswith("http"):
                    target_url = f"{self.target_instance.url}{target_url}"

                url_mapping[source_url] = target_url

                # Queue attachment mapping for batch commit
                if issue_mapping_id:
                    attachment_mapping = AttachmentMapping(
                        issue_mapping_id=issue_mapping_id,
                        source_url=source_url,
                        target_url=target_url,
                        filename=filename,
                        file_size=len(file_content),
                        uploaded_at=datetime.utcnow(),
                    )
                    attachment_mappings_to_add.append(attachment_mapping)

                logger.info(f"Synced attachment: {filename}")

            except Exception as e:
                logger.error(f"Failed to upload attachment {filename}: {e}")

        # Batch commit all attachment mappings
        if attachment_mappings_to_add:
            try:
                for mapping in attachment_mappings_to_add:
                    self.db.add(mapping)
                await self.db.commit()
                logger.debug(f"Committed {len(attachment_mappings_to_add)} attachment mappings")
            except Exception as e:
                logger.error(f"Failed to commit attachment mappings: {e}")
                await self.db.rollback()
                # Don't raise - attachments are synced on GitLab, just mapping failed

        # Replace URLs in description
        return replace_urls_in_description(description, url_mapping)

    async def cleanup_orphaned_resources(self) -> Dict[str, Any]:
        """
        Detect and report orphaned resources (issues created but not properly mapped).

        This method identifies:
        - Issues on target with Mirrored-From label but no database mapping
        - Comment mappings without valid parent issue mappings
        - Attachment mappings without valid parent issue mappings

        Returns:
            Dict with cleanup statistics
        """
        stats = {
            "orphaned_issues_found": 0,
            "orphaned_comments_found": 0,
            "orphaned_attachments_found": 0,
            "mappings_created": 0,
            "mappings_deleted": 0,
            "errors": []
        }

        logger.info(f"Starting resource cleanup for mirror {self.mirror.id}")

        try:
            # Find orphaned issues on target
            # Search both current and legacy label formats to catch issues
            # created before the upgrade to URL-based labels.
            cleanup_max_pages = min(10, settings.max_pages_per_request)
            target_issues = []
            for label_to_search in (self.mirror_from_label, self._mirror_from_label_legacy):
                batch = await self._execute_gitlab_api_call(
                    self.target_client.get_issues,
                    "cleanup_fetch_target_issues",
                    self.target_project_id,
                    labels=label_to_search,
                    state="all",
                    per_page=100,
                    get_all=True,
                    max_pages=cleanup_max_pages
                )
                target_issues.extend(batch)

            # Deduplicate by issue ID (an issue may match both label formats)
            seen_ids: Set[int] = set()
            unique_issues = []
            for issue in target_issues:
                issue_id = issue.get("id")
                if issue_id is not None and issue_id not in seen_ids:
                    seen_ids.add(issue_id)
                    unique_issues.append(issue)

            # Check each issue against database mappings
            for issue in unique_issues:
                target_issue_id = issue.get("id")
                target_issue_iid = issue.get("iid")
                if target_issue_id is None:
                    logger.warning("Skipping issue with missing 'id' field during cleanup")
                    continue

                # Check if mapping exists
                result = await self.db.execute(
                    select(IssueMapping).where(
                        IssueMapping.mirror_issue_config_id == self.config.id,
                        IssueMapping.target_issue_id == target_issue_id
                    )
                )
                mapping = result.scalar_one_or_none()

                if not mapping:
                    stats["orphaned_issues_found"] += 1
                    logger.warning(
                        f"Found orphaned issue {target_issue_iid} on target - "
                        "exists on GitLab but not in database"
                    )

            # Find orphaned comment mappings
            orphaned_comments = await self.db.execute(
                select(CommentMapping).where(
                    ~CommentMapping.issue_mapping_id.in_(
                        select(IssueMapping.id).where(
                            IssueMapping.mirror_issue_config_id == self.config.id
                        )
                    )
                )
            )
            orphaned_comment_list = orphaned_comments.scalars().all()
            stats["orphaned_comments_found"] = len(orphaned_comment_list)

            if orphaned_comment_list:
                logger.warning(
                    f"Found {len(orphaned_comment_list)} orphaned comment mappings - "
                    "deleting them from database"
                )
                for comment in orphaned_comment_list:
                    await self.db.delete(comment)
                    stats["mappings_deleted"] += 1
                await self.db.commit()

            # Find orphaned attachment mappings
            orphaned_attachments = await self.db.execute(
                select(AttachmentMapping).where(
                    ~AttachmentMapping.issue_mapping_id.in_(
                        select(IssueMapping.id).where(
                            IssueMapping.mirror_issue_config_id == self.config.id
                        )
                    )
                )
            )
            orphaned_attachment_list = orphaned_attachments.scalars().all()
            stats["orphaned_attachments_found"] = len(orphaned_attachment_list)

            if orphaned_attachment_list:
                logger.warning(
                    f"Found {len(orphaned_attachment_list)} orphaned attachment mappings - "
                    "deleting them from database"
                )
                for attachment in orphaned_attachment_list:
                    await self.db.delete(attachment)
                    stats["mappings_deleted"] += 1
                await self.db.commit()

            logger.info(
                f"Resource cleanup completed: "
                f"{stats['orphaned_issues_found']} orphaned issues, "
                f"{stats['mappings_deleted']} mappings deleted"
            )

        except Exception as e:
            logger.error(f"Resource cleanup failed: {e}")
            stats["errors"].append(str(e))

        return stats

    async def _find_existing_target_issue(
        self,
        source_issue_id: int,
        source_issue_iid: int
    ) -> Optional[Dict[str, Any]]:
        """
        Search for an existing issue on target that matches the source issue.

        This provides idempotency by finding orphaned issues (created but not mapped in DB).
        Searches for issues with the Mirrored-From label and matching source reference in footer.

        Args:
            source_issue_id: Source issue ID
            source_issue_iid: Source issue IID

        Returns:
            Existing issue dict if found, None otherwise
        """
        try:
            source_ref = f"{self.source_project_path}#{source_issue_iid}"

            # Search using current URL-based label first, then fall back to
            # legacy ID-based label for issues created before the upgrade.
            for label_to_search in (self.mirror_from_label, self._mirror_from_label_legacy):
                target_issues = await self._execute_gitlab_api_call(
                    self.target_client.get_issues,
                    f"search_existing_issue_{source_issue_iid}",
                    self.target_project_id,
                    labels=label_to_search,
                    state="all",
                    per_page=100,
                    get_all=True,
                    max_pages=settings.max_pages_per_request,
                )

                max_issues_to_check = settings.max_issues_per_sync
                issues_checked = 0

                for issue in target_issues:
                    if issues_checked >= max_issues_to_check:
                        logger.warning(
                            f"Searched {max_issues_to_check} issues without finding match for source #{source_issue_iid}. "
                            f"Consider increasing max_issues_per_sync if orphans are expected."
                        )
                        break

                    description = issue.get("description", "")
                    if source_ref in description and MIRROR_FOOTER_MARKER in description:
                        logger.debug(f"Found existing target issue {issue.get('iid')} for source {source_issue_iid}")
                        return issue

                    issues_checked += 1

            return None
        except Exception as e:
            # If search fails, log warning and continue (will create new issue)
            logger.warning(f"Failed to search for existing target issue: {e}")
            return None

    async def _load_target_labels_cache(self) -> None:
        """Load all target project labels into cache with retry logic."""
        labels = await self._execute_gitlab_api_call(
            self.target_client.get_project_labels,
            "load_target_labels",
            self.target_project_id
        )
        # Safely build cache, skipping any malformed labels
        self.target_labels_cache = {}
        for label in labels:
            label_name = label.get("name") if isinstance(label, dict) else None
            if label_name:
                self.target_labels_cache[label_name] = label
            else:
                logger.warning(f"Skipping label with missing 'name': {type(label).__name__}")
        logger.debug(f"Loaded {len(self.target_labels_cache)} labels into cache")

    async def _ensure_mirror_from_label(self) -> None:
        """Ensure the Mirrored-From label exists on target project."""
        if self.mirror_from_label not in self.target_labels_cache:
            # Create the label with retry logic
            try:
                label = await self._execute_gitlab_api_call(
                    self.target_client.create_label,
                    f"create_label_{self.mirror_from_label}",
                    self.target_project_id,
                    name=self.mirror_from_label,
                    color=MIRROR_FROM_LABEL_COLOR,
                    description=f"Issue mirrored from {self.source_instance.url}"
                )
                self.target_labels_cache[self.mirror_from_label] = label
                logger.info(f"Created Mirrored-From label: {self.mirror_from_label}")
            except Exception as e:
                logger.warning(f"Failed to create Mirrored-From label: {e}")

    @staticmethod
    def _seconds_to_duration(seconds: int) -> str:
        """Convert seconds to GitLab duration string (e.g., '3h30m')."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")

        return "".join(parts) if parts else "0m"

    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 datetime string.

        Returns a naive UTC datetime to match our DB columns
        (TIMESTAMP WITHOUT TIME ZONE).
        """
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            return dt.replace(tzinfo=None)
        except (ValueError, TypeError):
            return None
