import logging
import gitlab
from gitlab.exceptions import (
    GitlabAuthenticationError as GitlabAuthError,
    GitlabGetError,
    GitlabCreateError,
    GitlabDeleteError,
    GitlabUpdateError,
    GitlabHttpError,
)
from typing import List, Dict, Any, Optional
from app.core.encryption import encryption

logger = logging.getLogger(__name__)


class GitLabClientError(Exception):
    """Base exception for GitLab client errors."""
    pass


class GitLabAuthenticationError(GitLabClientError):
    """Raised when authentication fails (invalid or expired token)."""
    pass


class GitLabConnectionError(GitLabClientError):
    """Raised when connection to GitLab fails (network issues, server down)."""
    pass


class GitLabNotFoundError(GitLabClientError):
    """Raised when a resource is not found (project, mirror, etc.)."""
    pass


class GitLabPermissionError(GitLabClientError):
    """Raised when the token lacks required permissions."""
    pass


class GitLabRateLimitError(GitLabClientError):
    """Raised when rate limited by GitLab."""
    pass


class GitLabServerError(GitLabClientError):
    """Raised when GitLab returns a 5xx server error."""
    pass


def _handle_gitlab_error(e: Exception, operation: str) -> None:
    """
    Convert gitlab library exceptions to our custom exceptions with better messages.

    Args:
        e: The original exception
        operation: A description of what operation was being attempted

    Raises:
        GitLabClientError: An appropriate subclass based on the error type
    """
    error_msg = str(e)

    # Handle requests/urllib3 connection errors
    if isinstance(e, (ConnectionError, TimeoutError)):
        raise GitLabConnectionError(f"{operation}: Connection failed - {error_msg}")

    # Handle gitlab-specific exceptions
    if isinstance(e, GitlabAuthError):
        raise GitLabAuthenticationError(
            f"{operation}: Authentication failed - check that your token is valid and not expired"
        )

    if isinstance(e, GitlabGetError):
        if "404" in error_msg or "not found" in error_msg.lower():
            raise GitLabNotFoundError(f"{operation}: Resource not found - {error_msg}")
        if "401" in error_msg or "unauthorized" in error_msg.lower():
            raise GitLabAuthenticationError(f"{operation}: Authentication failed - {error_msg}")
        if "403" in error_msg or "forbidden" in error_msg.lower():
            raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")
        if "429" in error_msg:
            raise GitLabRateLimitError(f"{operation}: Rate limited by GitLab - try again later")

    if isinstance(e, GitlabCreateError):
        if "401" in error_msg or "unauthorized" in error_msg.lower():
            raise GitLabAuthenticationError(f"{operation}: Authentication failed - {error_msg}")
        if "403" in error_msg or "forbidden" in error_msg.lower():
            raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")
        if "409" in error_msg or "conflict" in error_msg.lower():
            raise GitLabClientError(f"{operation}: Conflict - resource already exists - {error_msg}")
        if "429" in error_msg:
            raise GitLabRateLimitError(f"{operation}: Rate limited by GitLab - try again later")

    if isinstance(e, GitlabDeleteError):
        if "404" in error_msg or "not found" in error_msg.lower():
            raise GitLabNotFoundError(f"{operation}: Resource not found - {error_msg}")
        if "403" in error_msg or "forbidden" in error_msg.lower():
            raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")

    if isinstance(e, GitlabUpdateError):
        if "404" in error_msg or "not found" in error_msg.lower():
            raise GitLabNotFoundError(f"{operation}: Resource not found - {error_msg}")
        if "403" in error_msg or "forbidden" in error_msg.lower():
            raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")

    if isinstance(e, GitlabHttpError):
        response_code = getattr(e, 'response_code', None)
        if response_code:
            if response_code == 401:
                raise GitLabAuthenticationError(f"{operation}: Authentication failed - {error_msg}")
            if response_code == 403:
                raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")
            if response_code == 404:
                raise GitLabNotFoundError(f"{operation}: Resource not found - {error_msg}")
            if response_code == 429:
                raise GitLabRateLimitError(f"{operation}: Rate limited by GitLab - try again later")
            if response_code >= 500:
                raise GitLabServerError(f"{operation}: GitLab server error ({response_code}) - {error_msg}")

    # Check error message for common patterns if we didn't match above
    lower_msg = error_msg.lower()
    if "connection" in lower_msg or "timeout" in lower_msg or "unreachable" in lower_msg:
        raise GitLabConnectionError(f"{operation}: Connection failed - {error_msg}")
    if "401" in error_msg or "unauthorized" in lower_msg:
        raise GitLabAuthenticationError(f"{operation}: Authentication failed - {error_msg}")
    if "403" in error_msg or "forbidden" in lower_msg:
        raise GitLabPermissionError(f"{operation}: Permission denied - {error_msg}")
    if "404" in error_msg or "not found" in lower_msg:
        raise GitLabNotFoundError(f"{operation}: Resource not found - {error_msg}")
    if "429" in error_msg or "rate limit" in lower_msg:
        raise GitLabRateLimitError(f"{operation}: Rate limited by GitLab - try again later")
    if "500" in error_msg or "502" in error_msg or "503" in error_msg or "504" in error_msg:
        raise GitLabServerError(f"{operation}: GitLab server error - {error_msg}")

    # Default: wrap in base error
    raise GitLabClientError(f"{operation}: {error_msg}")


class GitLabClient:
    """Wrapper for GitLab API interactions."""

    def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
        """
        Initialize GitLab client with URL and encrypted token.

        Args:
            url: GitLab instance URL
            encrypted_token: Encrypted API token
            timeout: Request timeout in seconds (default: 60)
        """
        self.url = url
        self.token = encryption.decrypt(encrypted_token)
        # Set timeout for all HTTP requests to prevent hanging operations
        self.gl = gitlab.Gitlab(url, private_token=self.token, timeout=timeout)

    def close(self) -> None:
        """
        Close the underlying HTTP session.

        This should be called when the client is no longer needed to release resources.
        """
        if hasattr(self.gl, 'session') and self.gl.session:
            self.gl.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures resources are cleaned up."""
        self.close()
        return False

    def test_connection(self) -> bool:
        """Test if the connection to GitLab is working.

        Returns True on success.
        Raises an exception with details on failure.
        """
        try:
            self.gl.auth()
            return True
        except Exception as e:
            logger.debug(f"GitLab connection test failed: {e}")
            raise

    def get_projects(
        self,
        search: Optional[str] = None,
        *,
        per_page: int = 50,
        page: int = 1,
        get_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get projects from GitLab.

        IMPORTANT: By default this does NOT fetch all pages (get_all=False) to
        avoid loading huge project lists in a single request. Set get_all=True
        explicitly if you really want the full list.
        """
        try:
            # python-gitlab: when get_all=False, pagination is controlled by page/per_page.
            # When get_all=True, python-gitlab will iterate through all pages.
            kwargs: Dict[str, Any] = {
                "search": search,
                "get_all": get_all,
                "per_page": per_page,
            }
            if not get_all:
                kwargs["page"] = page

            projects = self.gl.projects.list(**kwargs)
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "path": p.path,
                    "path_with_namespace": p.path_with_namespace,
                    "description": p.description if hasattr(p, "description") else "",
                    "http_url_to_repo": p.http_url_to_repo,
                    "ssh_url_to_repo": p.ssh_url_to_repo,
                }
                for p in projects
            ]
        except Exception as e:
            _handle_gitlab_error(e, "Failed to fetch projects")

    def get_project(self, project_id: int) -> Dict[str, Any]:
        """Get a specific project by ID."""
        try:
            p = self.gl.projects.get(project_id)
            return {
                "id": p.id,
                "name": p.name,
                "path": p.path,
                "path_with_namespace": p.path_with_namespace,
                "description": p.description if hasattr(p, "description") else "",
                "http_url_to_repo": p.http_url_to_repo,
                "ssh_url_to_repo": p.ssh_url_to_repo,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch project {project_id}")

    def get_project_by_path(self, project_path: str) -> Dict[str, Any]:
        """
        Get a specific project by its path_with_namespace.

        Args:
            project_path: Full project path including namespace (e.g., 'group/subgroup/project')

        Returns:
            Dict with project info including 'id' and 'path_with_namespace'

        Raises:
            GitLabNotFoundError: If project doesn't exist
            GitLabClientError: For other errors
        """
        try:
            # GitLab API supports getting projects by path (URL-encoded)
            # python-gitlab handles the encoding automatically
            p = self.gl.projects.get(project_path)
            return {
                "id": p.id,
                "name": p.name,
                "path": p.path,
                "path_with_namespace": p.path_with_namespace,
                "description": p.description if hasattr(p, "description") else "",
                "http_url_to_repo": p.http_url_to_repo,
                "ssh_url_to_repo": p.ssh_url_to_repo,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch project by path '{project_path}'")

    def get_groups(
        self,
        search: Optional[str] = None,
        *,
        per_page: int = 50,
        page: int = 1,
        get_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get groups from GitLab.

        IMPORTANT: By default this does NOT fetch all pages (get_all=False) to
        avoid loading huge group lists in a single request. Set get_all=True
        explicitly if you really want the full list.
        """
        try:
            kwargs: Dict[str, Any] = {
                "search": search,
                "get_all": get_all,
                "per_page": per_page,
            }
            if not get_all:
                kwargs["page"] = page

            groups = self.gl.groups.list(**kwargs)
            return [
                {
                    "id": g.id,
                    "name": g.name,
                    "path": g.path,
                    "full_path": g.full_path,
                    "description": g.description if hasattr(g, "description") else "",
                }
                for g in groups
            ]
        except Exception as e:
            _handle_gitlab_error(e, "Failed to fetch groups")

    def get_current_user(self) -> Dict[str, Any]:
        """Get the user associated with the API token."""
        try:
            u = self.gl.http_get("/user")
            if not isinstance(u, dict):
                raise GitLabClientError("Failed to fetch current user: Unexpected response from GitLab API")
            return {
                "id": u.get("id"),
                "username": u.get("username"),
                "name": u.get("name"),
            }
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, "Failed to fetch current user")

    def create_pull_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        only_protected_branches: bool = False,
        mirror_overwrites_diverged_branches: bool | None = None,
        trigger_builds: bool | None = None,
        mirror_branch_regex: str | None = None,
        auth_user: str | None = None,
        auth_password: str | None = None,
    ) -> Dict[str, Any]:
        """
        Create/configure a pull mirror for a project (target pulls from source).

        Tries two approaches for maximum compatibility:
        1. PUT /projects/:id/mirror/pull (GitLab 17.6+, dedicated endpoint)
        2. PUT /projects/:id with mirror=true and import_url (all GitLab versions)

        For approach 1, auth can be passed separately via auth_user/auth_password.
        For approach 2, credentials must be embedded in the mirror_url.

        Args:
            project_id: The project ID to configure pull mirroring on.
            mirror_url: The URL of the source repository to pull from.
                        Should include embedded credentials for compatibility
                        with older GitLab versions.
            enabled: Whether the mirror is enabled.
            only_protected_branches: If True, only mirror protected branches.
            mirror_overwrites_diverged_branches: If True, overwrite diverged branches on pull.
            trigger_builds: If True, trigger CI/CD pipelines when mirror updates.
            mirror_branch_regex: Regex pattern to filter which branches to mirror.
            auth_user: Username for authentication (used by GitLab 17.6+ endpoint).
            auth_password: Password/token for authentication (used by GitLab 17.6+ endpoint).

        Returns:
            Dict with pull mirror configuration details.
        """
        # --- Attempt 1: Dedicated pull mirror endpoint (GitLab 17.6+) ---
        try:
            data: Dict[str, Any] = {
                "url": mirror_url,
                "enabled": enabled,
            }

            # Add authentication if provided
            if auth_user is not None:
                data["auth_user"] = auth_user
            if auth_password is not None:
                data["auth_password"] = auth_password

            # Add optional settings
            if only_protected_branches is not None:
                data["only_mirror_protected_branches"] = only_protected_branches
            if mirror_overwrites_diverged_branches is not None:
                data["mirror_overwrites_diverged_branches"] = mirror_overwrites_diverged_branches
            if trigger_builds is not None:
                data["mirror_trigger_builds"] = trigger_builds
            if mirror_branch_regex is not None:
                data["mirror_branch_regex"] = mirror_branch_regex

            # PUT /projects/:id/mirror/pull (GitLab 17.6+)
            result = self.gl.http_put(f"/projects/{project_id}/mirror/pull", post_data=data)

            if isinstance(result, dict):
                return {
                    "id": result.get("id"),
                    "url": result.get("url"),
                    "enabled": result.get("enabled"),
                    "update_status": result.get("update_status"),
                    "last_update_at": result.get("last_update_at"),
                    "last_successful_update_at": result.get("last_successful_update_at"),
                    "last_error": result.get("last_error"),
                    "only_mirror_protected_branches": result.get("only_mirror_protected_branches"),
                    "mirror_overwrites_diverged_branches": result.get("mirror_overwrites_diverged_branches"),
                    "mirror_trigger_builds": result.get("mirror_trigger_builds"),
                    "mirror_branch_regex": result.get("mirror_branch_regex"),
                }
        except Exception as e:
            logger.info(
                f"Dedicated pull mirror endpoint (PUT mirror/pull) not available for "
                f"project {project_id} ({type(e).__name__}). "
                f"Falling back to Projects API."
            )

        # --- Attempt 2: Projects API (all GitLab versions with pull mirror support) ---
        # Uses PUT /projects/:id with mirror=true and import_url.
        # Credentials must be embedded in the import_url for this approach.
        try:
            project_data: Dict[str, Any] = {
                "mirror": enabled,
                "import_url": mirror_url,
            }

            if only_protected_branches is not None:
                project_data["only_mirror_protected_branches"] = only_protected_branches
            if mirror_overwrites_diverged_branches is not None:
                project_data["mirror_overwrites_diverged_branches"] = mirror_overwrites_diverged_branches
            if trigger_builds is not None:
                project_data["mirror_trigger_builds"] = trigger_builds
            if mirror_branch_regex is not None:
                project_data["mirror_branch_regex"] = mirror_branch_regex

            result = self.gl.http_put(f"/projects/{project_id}", post_data=project_data)

            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to configure pull mirror on project {project_id}: "
                    f"Unexpected response from GitLab API"
                )

            return {
                "id": None,  # No separate mirror ID when using Projects API
                "url": result.get("import_url"),
                "enabled": result.get("mirror", False),
                "update_status": None,
                "last_update_at": None,
                "last_successful_update_at": None,
                "last_error": None,
                "only_mirror_protected_branches": result.get("only_mirror_protected_branches"),
                "mirror_overwrites_diverged_branches": result.get("mirror_overwrites_diverged_branches"),
                "mirror_trigger_builds": result.get("mirror_trigger_builds"),
                "mirror_branch_regex": result.get("mirror_branch_regex"),
            }
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create pull mirror on project {project_id}")

    def create_push_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        keep_divergent_refs: bool | None = None,
        only_protected_branches: bool = False,
        mirror_branch_regex: str | None = None,
    ) -> Dict[str, Any]:
        """
        Create a push mirror for a project (source pushes to target).

        Uses the remote mirrors API: POST /projects/:id/remote_mirrors
        """
        try:
            return self._create_remote_mirror(
                project_id=project_id,
                mirror_url=mirror_url,
                enabled=enabled,
                only_protected_branches=only_protected_branches,
                keep_divergent_refs=keep_divergent_refs,
                mirror_branch_regex=mirror_branch_regex,
            )
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create push mirror on project {project_id}")

    def get_project_mirrors(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all push mirrors (remote mirrors) for a project.

        Note: This only returns push mirrors. Use get_pull_mirror() to get
        pull mirror configuration.
        """
        try:
            mirrors = self.gl.http_get(f"/projects/{project_id}/remote_mirrors")
            if not isinstance(mirrors, list):
                return []

            out: List[Dict[str, Any]] = []
            for m in mirrors:
                if not isinstance(m, dict):
                    continue
                out.append({
                    "id": m.get("id"),
                    "url": m.get("url"),
                    "enabled": m.get("enabled"),
                    "mirror_direction": "push",  # remote_mirrors are always push
                    "only_protected_branches": m.get("only_protected_branches"),
                    "keep_divergent_refs": m.get("keep_divergent_refs"),
                    "mirror_branch_regex": m.get("mirror_branch_regex"),
                    "update_status": m.get("update_status"),
                    "last_update_at": m.get("last_update_at"),
                    "last_successful_update_at": m.get("last_successful_update_at"),
                    "last_error": m.get("last_error"),
                })
            return out
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch push mirrors for project {project_id}")

    def get_pull_mirror(self, project_id: int) -> Dict[str, Any] | None:
        """
        Get pull mirror configuration for a project.

        Uses the dedicated pull mirror API: GET /projects/:id/mirror/pull

        Returns:
            Dict with pull mirror config, or None if no pull mirror is configured.
        """
        try:
            result = self.gl.http_get(f"/projects/{project_id}/mirror/pull")
            if not isinstance(result, dict):
                return None

            # Check if pull mirroring is actually configured
            # If there's no URL, the project doesn't have pull mirroring set up
            if not result.get("url"):
                return None

            return {
                "id": result.get("id"),
                "url": result.get("url"),
                "enabled": result.get("enabled"),
                "mirror_direction": "pull",
                "update_status": result.get("update_status"),
                "last_update_at": result.get("last_update_at"),
                "last_successful_update_at": result.get("last_successful_update_at"),
                "last_error": result.get("last_error"),
                "only_mirror_protected_branches": result.get("only_mirror_protected_branches"),
                "mirror_overwrites_diverged_branches": result.get("mirror_overwrites_diverged_branches"),
                "mirror_trigger_builds": result.get("mirror_trigger_builds"),
                "mirror_branch_regex": result.get("mirror_branch_regex"),
            }
        except Exception as e:
            # 404 or 400 "not mirrored" means no pull mirror configured - return None
            error_msg = str(e).lower()
            if "404" in error_msg or "not found" in error_msg:
                return None
            if "400" in error_msg and "not mirrored" in error_msg:
                return None
            _handle_gitlab_error(e, f"Failed to fetch pull mirror for project {project_id}")

    def trigger_mirror_update(self, project_id: int, mirror_id: int) -> bool:
        """
        Trigger an immediate update of a push mirror.

        Note: For pull mirrors, use trigger_pull_mirror_update() instead.
        """
        try:
            # GitLab: POST /projects/:id/remote_mirrors/:mirror_id/sync
            self.gl.http_post(f"/projects/{project_id}/remote_mirrors/{mirror_id}/sync")
            return True
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to trigger push mirror update for mirror {mirror_id} on project {project_id}")

    def trigger_pull_mirror_update(self, project_id: int) -> bool:
        """
        Trigger an immediate update of a pull mirror.

        Uses the dedicated pull mirror API: POST /projects/:id/mirror/pull

        Returns:
            True if sync was triggered, False if no pull mirror is configured.
        """
        try:
            self.gl.http_post(f"/projects/{project_id}/mirror/pull")
            return True
        except Exception as e:
            # 400 "not mirrored" means pull mirror was removed externally.
            # Return False instead of raising so the circuit breaker isn't tripped.
            error_msg = str(e).lower()
            if "400" in error_msg and "not mirrored" in error_msg:
                return False
            _handle_gitlab_error(e, f"Failed to trigger pull mirror update for project {project_id}")

    def delete_mirror(self, project_id: int, mirror_id: int) -> bool:
        """
        Delete a push mirror (remote mirror).

        Note: For pull mirrors, use delete_pull_mirror() instead.
        """
        try:
            self.gl.http_delete(f"/projects/{project_id}/remote_mirrors/{mirror_id}")
            return True
        except Exception as e:
            # 404 is acceptable - mirror was already deleted
            error_msg = str(e).lower()
            if "404" in error_msg or "not found" in error_msg:
                return True
            _handle_gitlab_error(e, f"Failed to delete push mirror {mirror_id} from project {project_id}")

    def delete_pull_mirror(self, project_id: int) -> bool:
        """
        Delete/disable pull mirror configuration for a project.

        Tries two approaches:
        1. PUT /projects/:id/mirror/pull with enabled=false (GitLab 17.6+)
        2. PUT /projects/:id with mirror=false (all versions)
        """
        # --- Attempt 1: Dedicated endpoint (GitLab 17.6+) ---
        try:
            self.gl.http_put(
                f"/projects/{project_id}/mirror/pull",
                post_data={"enabled": False}
            )
            return True
        except Exception as e:
            error_msg = str(e).lower()
            # "not mirrored" means no pull mirror exists — that's fine, nothing to delete
            if "400" in error_msg and "not mirrored" in error_msg:
                return True
            # 404 means the dedicated endpoint doesn't exist on this GitLab version,
            # so we must fall through to the Projects API to actually disable mirroring.
            logger.info(
                f"Dedicated pull mirror endpoint not available for project {project_id}, "
                f"falling back to Projects API for disable."
            )

        # --- Attempt 2: Projects API (all versions) ---
        try:
            self.gl.http_put(
                f"/projects/{project_id}",
                post_data={"mirror": False}
            )
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if "404" in error_msg or "not found" in error_msg:
                return True
            if "400" in error_msg and "not mirrored" in error_msg:
                return True
            _handle_gitlab_error(e, f"Failed to delete pull mirror from project {project_id}")

    def update_mirror(
        self,
        project_id: int,
        mirror_id: int,
        url: Optional[str] = None,
        enabled: Optional[bool] = None,
        only_protected_branches: Optional[bool] = None,
        keep_divergent_refs: Optional[bool] = None,
        mirror_branch_regex: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update push mirror (remote mirror) settings.

        Note: For pull mirrors, use update_pull_mirror() instead.

        Args:
            project_id: The project ID.
            mirror_id: The push mirror ID.
            url: New authenticated URL (used for token rotation).
            enabled: Whether the mirror is enabled.
            only_protected_branches: If True, only mirror protected branches.
            keep_divergent_refs: If True, keep divergent refs on target.
            mirror_branch_regex: Regex pattern to filter which branches to mirror.
        """
        try:
            data: Dict[str, Any] = {}
            if url is not None:
                data["url"] = url
            if enabled is not None:
                data["enabled"] = enabled
            if only_protected_branches is not None:
                data["only_protected_branches"] = only_protected_branches
            if keep_divergent_refs is not None:
                data["keep_divergent_refs"] = keep_divergent_refs
            if mirror_branch_regex is not None:
                data["mirror_branch_regex"] = mirror_branch_regex

            if not data:
                return {"id": mirror_id}

            result = self.gl.http_put(
                f"/projects/{project_id}/remote_mirrors/{mirror_id}",
                post_data=data,
            )

            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to update push mirror {mirror_id} on project {project_id}: Unexpected response from GitLab API"
                )

            return result
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to update push mirror {mirror_id} on project {project_id}")

    def update_pull_mirror(
        self,
        project_id: int,
        url: Optional[str] = None,
        enabled: Optional[bool] = None,
        auth_user: Optional[str] = None,
        auth_password: Optional[str] = None,
        only_mirror_protected_branches: Optional[bool] = None,
        mirror_overwrites_diverged_branches: Optional[bool] = None,
        mirror_trigger_builds: Optional[bool] = None,
        mirror_branch_regex: Optional[str] = None,
        import_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update pull mirror configuration.

        Tries two approaches:
        1. PUT /projects/:id/mirror/pull (GitLab 17.6+, supports separate auth)
        2. PUT /projects/:id with import_url (all versions, credentials in URL)

        Args:
            project_id: The project ID.
            url: New source repository URL (for 17.6+ endpoint).
            enabled: Whether the mirror is enabled.
            auth_user: Username for authentication (for 17.6+ endpoint).
            auth_password: Password/token for authentication (for 17.6+ endpoint).
            only_mirror_protected_branches: If True, only mirror protected branches.
            mirror_overwrites_diverged_branches: If True, overwrite diverged branches.
            mirror_trigger_builds: If True, trigger CI/CD on updates.
            mirror_branch_regex: Regex pattern to filter branches.
            import_url: Authenticated URL with embedded credentials (for Projects API fallback).
        """
        if not any([url, enabled is not None, auth_user, auth_password,
                     only_mirror_protected_branches is not None,
                     mirror_overwrites_diverged_branches is not None,
                     mirror_trigger_builds is not None, mirror_branch_regex,
                     import_url]):
            # No changes requested
            return self.get_pull_mirror(project_id) or {}

        # --- Attempt 1: Dedicated endpoint (GitLab 17.6+) ---
        try:
            data: Dict[str, Any] = {}
            if url is not None:
                data["url"] = url
            if enabled is not None:
                data["enabled"] = enabled
            if auth_user is not None:
                data["auth_user"] = auth_user
            if auth_password is not None:
                data["auth_password"] = auth_password
            if only_mirror_protected_branches is not None:
                data["only_mirror_protected_branches"] = only_mirror_protected_branches
            if mirror_overwrites_diverged_branches is not None:
                data["mirror_overwrites_diverged_branches"] = mirror_overwrites_diverged_branches
            if mirror_trigger_builds is not None:
                data["mirror_trigger_builds"] = mirror_trigger_builds
            if mirror_branch_regex is not None:
                data["mirror_branch_regex"] = mirror_branch_regex

            if data:
                result = self.gl.http_put(
                    f"/projects/{project_id}/mirror/pull",
                    post_data=data,
                )

                if isinstance(result, dict):
                    return {
                        "id": result.get("id"),
                        "url": result.get("url"),
                        "enabled": result.get("enabled"),
                        "update_status": result.get("update_status"),
                        "last_update_at": result.get("last_update_at"),
                        "last_successful_update_at": result.get("last_successful_update_at"),
                        "last_error": result.get("last_error"),
                        "only_mirror_protected_branches": result.get("only_mirror_protected_branches"),
                        "mirror_overwrites_diverged_branches": result.get("mirror_overwrites_diverged_branches"),
                        "mirror_trigger_builds": result.get("mirror_trigger_builds"),
                        "mirror_branch_regex": result.get("mirror_branch_regex"),
                    }
        except Exception as e:
            error_msg = str(e).lower()
            if "400" in error_msg and "not mirrored" in error_msg:
                raise GitLabNotFoundError(
                    f"Pull mirror is not configured on project {project_id}"
                )
            logger.info(
                f"Dedicated pull mirror endpoint not available for project {project_id}, "
                f"falling back to Projects API for update."
            )

        # --- Attempt 2: Projects API (all versions) ---
        try:
            project_data: Dict[str, Any] = {}
            if import_url is not None:
                project_data["import_url"] = import_url
            elif url is not None:
                project_data["import_url"] = url
            if enabled is not None:
                project_data["mirror"] = enabled
            if only_mirror_protected_branches is not None:
                project_data["only_mirror_protected_branches"] = only_mirror_protected_branches
            if mirror_overwrites_diverged_branches is not None:
                project_data["mirror_overwrites_diverged_branches"] = mirror_overwrites_diverged_branches
            if mirror_trigger_builds is not None:
                project_data["mirror_trigger_builds"] = mirror_trigger_builds
            if mirror_branch_regex is not None:
                project_data["mirror_branch_regex"] = mirror_branch_regex

            if not project_data:
                return self.get_pull_mirror(project_id) or {}

            result = self.gl.http_put(
                f"/projects/{project_id}",
                post_data=project_data,
            )

            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to update pull mirror on project {project_id}: Unexpected response"
                )

            return {
                "id": None,
                "url": result.get("import_url"),
                "enabled": result.get("mirror", False),
                "update_status": None,
                "last_update_at": None,
                "last_successful_update_at": None,
                "last_error": None,
                "only_mirror_protected_branches": result.get("only_mirror_protected_branches"),
                "mirror_overwrites_diverged_branches": result.get("mirror_overwrites_diverged_branches"),
                "mirror_trigger_builds": result.get("mirror_trigger_builds"),
                "mirror_branch_regex": result.get("mirror_branch_regex"),
            }
        except GitLabClientError:
            raise
        except Exception as e:
            error_msg = str(e).lower()
            if "400" in error_msg and "not mirrored" in error_msg:
                raise GitLabNotFoundError(
                    f"Pull mirror is not configured on project {project_id}"
                )
            _handle_gitlab_error(e, f"Failed to update pull mirror on project {project_id}")

    def _create_remote_mirror(
        self,
        *,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        only_protected_branches: bool = False,
        keep_divergent_refs: bool | None = None,
        mirror_branch_regex: str | None = None,
    ) -> Dict[str, Any]:
        """
        Create a push mirror (remote mirror) using the GitLab API.

        Note: This method is for PUSH mirrors only. The /remote_mirrors endpoint
        only supports push mirroring. For pull mirrors, use create_pull_mirror().

        Args:
            project_id: The project ID to create the mirror on.
            mirror_url: The authenticated URL to push to.
            enabled: Whether the mirror is enabled.
            only_protected_branches: If True, only mirror protected branches.
            keep_divergent_refs: If True, keep divergent refs on target.
            mirror_branch_regex: Regex pattern to filter which branches to mirror
                                 (Premium/Ultimate only).
        """
        data: Dict[str, Any] = {
            "url": mirror_url,
            "enabled": enabled,
            "only_protected_branches": only_protected_branches,
        }
        if keep_divergent_refs is not None:
            data["keep_divergent_refs"] = keep_divergent_refs
        if mirror_branch_regex is not None:
            data["mirror_branch_regex"] = mirror_branch_regex

        result = self.gl.http_post(f"/projects/{project_id}/remote_mirrors", post_data=data)

        if not isinstance(result, dict):
            raise GitLabClientError(
                f"Failed to create push mirror on project {project_id}: Unexpected response from GitLab API"
            )

        return result

    # -------------------------------------------------------------------------
    # File Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def create_file(
        self,
        project_id: int,
        file_path: str,
        content: str,
        branch: str,
        commit_message: str,
        author_email: Optional[str] = None,
        author_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a file in a repository."""
        try:
            project = self.gl.projects.get(project_id)
            try:
                # Try to get existing file first
                existing = project.files.get(file_path=file_path, ref=branch)
                # File exists, update it
                existing.content = content
                existing.save(branch=branch, commit_message=commit_message)
                return {"file_path": file_path, "branch": branch, "action": "updated"}
            except gitlab.exceptions.GitlabGetError:
                # File doesn't exist, create it
                create_data: Dict[str, Any] = {
                    "file_path": file_path,
                    "branch": branch,
                    "content": content,
                    "commit_message": commit_message,
                }
                if author_email:
                    create_data["author_email"] = author_email
                if author_name:
                    create_data["author_name"] = author_name
                project.files.create(create_data)
                return {"file_path": file_path, "branch": branch, "action": "created"}
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create/update file {file_path} on project {project_id}")

    def get_file(
        self,
        project_id: int,
        file_path: str,
        ref: str = "main",
    ) -> Dict[str, Any]:
        """Get a file from a repository."""
        try:
            project = self.gl.projects.get(project_id)
            f = project.files.get(file_path=file_path, ref=ref)
            return {
                "file_path": f.file_path,
                "content": f.decode().decode("utf-8"),
                "size": f.size,
                "encoding": f.encoding,
                "ref": ref,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get file {file_path} from project {project_id}")

    # -------------------------------------------------------------------------
    # Branch Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def create_branch(
        self,
        project_id: int,
        branch_name: str,
        ref: str = "main",
    ) -> Dict[str, Any]:
        """Create a new branch from a reference."""
        try:
            project = self.gl.projects.get(project_id)
            branch = project.branches.create({"branch": branch_name, "ref": ref})
            commit_sha = branch.commit.get("id") if isinstance(branch.commit, dict) else getattr(branch.commit, "id", None)
            return {
                "name": branch.name,
                "commit_sha": commit_sha,
                "protected": branch.protected,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create branch {branch_name} on project {project_id}")

    def get_branches(self, project_id: int) -> List[Dict[str, Any]]:
        """List all branches in a project."""
        try:
            project = self.gl.projects.get(project_id)
            branches = project.branches.list(get_all=True)
            result = []
            for b in branches:
                commit_sha = b.commit.get("id") if isinstance(b.commit, dict) else getattr(b.commit, "id", None)
                result.append({
                    "name": b.name,
                    "commit_sha": commit_sha,
                    "protected": b.protected,
                    "default": getattr(b, "default", False),
                })
            return result
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get branches for project {project_id}")

    def protect_branch(
        self,
        project_id: int,
        branch_name: str,
        push_access_level: int = 40,  # Maintainers
        merge_access_level: int = 40,
    ) -> Dict[str, Any]:
        """Protect a branch."""
        try:
            project = self.gl.projects.get(project_id)
            protection = project.protectedbranches.create({
                "name": branch_name,
                "push_access_level": push_access_level,
                "merge_access_level": merge_access_level,
            })
            return {"name": protection.name, "protected": True}
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to protect branch {branch_name} on project {project_id}")

    # -------------------------------------------------------------------------
    # Tag Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def create_tag(
        self,
        project_id: int,
        tag_name: str,
        ref: str,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a tag."""
        try:
            project = self.gl.projects.get(project_id)
            data: Dict[str, Any] = {"tag_name": tag_name, "ref": ref}
            if message:
                data["message"] = message
            tag = project.tags.create(data)
            commit_sha = tag.commit.get("id") if isinstance(tag.commit, dict) else getattr(tag.commit, "id", None)
            return {
                "name": tag.name,
                "commit_sha": commit_sha,
                "message": getattr(tag, "message", None),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create tag {tag_name} on project {project_id}")

    def get_tags(self, project_id: int) -> List[Dict[str, Any]]:
        """List all tags in a project."""
        try:
            project = self.gl.projects.get(project_id)
            tags = project.tags.list(get_all=True)
            result = []
            for t in tags:
                commit_sha = t.commit.get("id") if isinstance(t.commit, dict) else getattr(t.commit, "id", None)
                result.append({
                    "name": t.name,
                    "commit_sha": commit_sha,
                    "message": getattr(t, "message", None),
                })
            return result
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get tags for project {project_id}")

    # -------------------------------------------------------------------------
    # Commit Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def get_commits(
        self,
        project_id: int,
        ref_name: str = "main",
        per_page: int = 20,
    ) -> List[Dict[str, Any]]:
        """List commits on a branch."""
        try:
            project = self.gl.projects.get(project_id)
            commits = project.commits.list(ref_name=ref_name, per_page=per_page)
            return [
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "title": c.title,
                    "message": c.message,
                    "author_name": c.author_name,
                    "authored_date": c.authored_date,
                }
                for c in commits
            ]
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get commits for project {project_id}")

    def get_commit(self, project_id: int, commit_sha: str) -> Dict[str, Any]:
        """Get a specific commit."""
        try:
            project = self.gl.projects.get(project_id)
            commit = project.commits.get(commit_sha)
            return {
                "id": commit.id,
                "short_id": commit.short_id,
                "title": commit.title,
                "message": commit.message,
                "author_name": commit.author_name,
                "authored_date": commit.authored_date,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get commit {commit_sha} from project {project_id}")

    def create_commit(
        self,
        project_id: int,
        branch: str,
        commit_message: str,
        actions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Create a commit with multiple file actions.

        Actions format:
        [
            {"action": "create", "file_path": "foo.txt", "content": "..."},
            {"action": "update", "file_path": "bar.txt", "content": "..."},
            {"action": "delete", "file_path": "baz.txt"},
        ]
        """
        try:
            project = self.gl.projects.get(project_id)
            commit = project.commits.create({
                "branch": branch,
                "commit_message": commit_message,
                "actions": actions,
            })
            return {
                "id": commit.id,
                "short_id": commit.short_id,
                "title": commit.title,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create commit on project {project_id}")

    # -------------------------------------------------------------------------
    # Group Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def create_group(
        self,
        name: str,
        path: str,
        parent_id: Optional[int] = None,
        visibility: str = "private",
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a group or subgroup."""
        try:
            data: Dict[str, Any] = {
                "name": name,
                "path": path,
                "visibility": visibility,
            }
            if parent_id:
                data["parent_id"] = parent_id
            if description:
                data["description"] = description

            group = self.gl.groups.create(data)
            return {
                "id": group.id,
                "name": group.name,
                "path": group.path,
                "full_path": group.full_path,
                "visibility": group.visibility,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create group {name}")

    def delete_group(self, group_id: int) -> bool:
        """Delete a group."""
        try:
            self.gl.groups.delete(group_id)
            return True
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to delete group {group_id}")

    def get_group(self, group_id_or_path: int | str) -> Dict[str, Any]:
        """Get a group by ID or path."""
        try:
            group = self.gl.groups.get(group_id_or_path)
            return {
                "id": group.id,
                "name": group.name,
                "path": group.path,
                "full_path": group.full_path,
                "visibility": group.visibility,
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get group {group_id_or_path}")

    # -------------------------------------------------------------------------
    # Project Operations (for E2E testing)
    # -------------------------------------------------------------------------

    def create_project(
        self,
        name: str,
        path: str,
        namespace_id: int,
        visibility: str = "private",
        initialize_with_readme: bool = False,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a project in a namespace."""
        try:
            data: Dict[str, Any] = {
                "name": name,
                "path": path,
                "namespace_id": namespace_id,
                "visibility": visibility,
                "initialize_with_readme": initialize_with_readme,
            }
            if description:
                data["description"] = description

            project = self.gl.projects.create(data)
            return {
                "id": project.id,
                "name": project.name,
                "path": project.path,
                "path_with_namespace": project.path_with_namespace,
                "http_url_to_repo": project.http_url_to_repo,
                "ssh_url_to_repo": getattr(project, "ssh_url_to_repo", None),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create project {name}")

    def delete_project(self, project_id: int) -> bool:
        """Delete a project."""
        try:
            self.gl.projects.delete(project_id)
            return True
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to delete project {project_id}")

    # -------------------------------------------------------------------------
    # Project Access Token Operations (for automatic mirror authentication)
    # -------------------------------------------------------------------------

    def create_project_access_token(
        self,
        project_id: int,
        name: str,
        scopes: List[str],
        expires_at: str,
        access_level: int = 40,  # Maintainer by default
    ) -> Dict[str, Any]:
        """
        Create a project access token.

        Args:
            project_id: The project ID to create the token for.
            name: A descriptive name for the token.
            scopes: List of scopes (e.g., ["read_repository", "write_repository"]).
            expires_at: Expiration date in YYYY-MM-DD format.
            access_level: Access level (10=Guest, 20=Reporter, 30=Developer, 40=Maintainer).

        Returns:
            Dict with token details including the plaintext token value (only available on creation).
        """
        try:
            data = {
                "name": name,
                "scopes": scopes,
                "expires_at": expires_at,
                "access_level": access_level,
            }
            result = self.gl.http_post(
                f"/projects/{project_id}/access_tokens",
                post_data=data,
            )
            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to create project access token on project {project_id}: Unexpected response from GitLab API"
                )
            return {
                "id": result.get("id"),
                "name": result.get("name"),
                "token": result.get("token"),  # Plaintext token, only returned on creation
                "scopes": result.get("scopes"),
                "expires_at": result.get("expires_at"),
                "access_level": result.get("access_level"),
                "active": result.get("active"),
            }
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create project access token on project {project_id}")

    def delete_project_access_token(self, project_id: int, token_id: int) -> bool:
        """
        Revoke/delete a project access token.

        Args:
            project_id: The project ID.
            token_id: The token ID to revoke.

        Returns:
            True if successful.
        """
        try:
            self.gl.http_delete(f"/projects/{project_id}/access_tokens/{token_id}")
            return True
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to delete project access token {token_id} from project {project_id}")

    def rotate_project_access_token(
        self,
        project_id: int,
        token_id: int,
        expires_at: str,
    ) -> Dict[str, Any]:
        """
        Rotate a project access token (revoke old, create new with same settings).

        GitLab 16.0+ has a native rotate endpoint, but for compatibility we
        implement this as delete + create with the same parameters.

        Args:
            project_id: The project ID.
            token_id: The existing token ID to rotate.
            expires_at: New expiration date in YYYY-MM-DD format.

        Returns:
            Dict with new token details.
        """
        try:
            # Try native rotation first (GitLab 16.0+)
            result = self.gl.http_post(
                f"/projects/{project_id}/access_tokens/{token_id}/rotate",
                post_data={"expires_at": expires_at},
            )
            if isinstance(result, dict):
                return {
                    "id": result.get("id"),
                    "name": result.get("name"),
                    "token": result.get("token"),
                    "scopes": result.get("scopes"),
                    "expires_at": result.get("expires_at"),
                    "access_level": result.get("access_level"),
                    "active": result.get("active"),
                }
            raise GitLabClientError(
                f"Failed to rotate project access token {token_id} on project {project_id}: Unexpected response from rotation endpoint"
            )
        except GitLabClientError:
            raise
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to rotate project access token {token_id} on project {project_id}")

    # -------------------------------------------------------------------------
    # Issue Operations (for issue mirroring)
    # -------------------------------------------------------------------------

    def get_issues(
        self,
        project_id: int,
        *,
        updated_after: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[str] = None,
        per_page: int = 100,
        page: int = 1,
        get_all: bool = False,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get issues from a project with optional filtering.

        Args:
            project_id: The project ID.
            updated_after: ISO 8601 datetime string to filter issues updated after this time.
            state: Filter by state ('opened', 'closed', 'all').
            labels: Comma-separated label names to filter by.
            per_page: Number of issues per page.
            page: Page number (only used when get_all=False).
            get_all: If True, fetch all pages of issues. If False, fetch only one page.
            max_pages: Maximum number of pages to fetch (only used when get_all=True).
                      Prevents unlimited fetching. None = no limit.

        Returns:
            List of issue dictionaries.
        """
        try:
            params: Dict[str, Any] = {
                "per_page": per_page,
            }
            if updated_after:
                params["updated_after"] = updated_after
            if state:
                params["state"] = state
            if labels:
                params["labels"] = labels

            result = []

            if get_all:
                # Fetch all pages (or up to max_pages if specified)
                current_page = 1
                while True:
                    # Check if we've reached the max_pages limit
                    if max_pages is not None and current_page > max_pages:
                        logger.warning(
                            f"Reached max_pages limit ({max_pages}) for project {project_id}. "
                            f"Fetched {len(result)} issues so far."
                        )
                        break

                    params["page"] = current_page
                    issues = self.gl.http_get(f"/projects/{project_id}/issues", query_data=params)

                    if not isinstance(issues, list) or len(issues) == 0:
                        break

                    for issue in issues:
                        if not isinstance(issue, dict):
                            continue
                        result.append({
                            "id": issue.get("id"),
                            "iid": issue.get("iid"),
                            "title": issue.get("title"),
                            "description": issue.get("description"),
                            "state": issue.get("state"),
                            "labels": issue.get("labels", []),
                            "milestone": issue.get("milestone"),
                            "iteration": issue.get("iteration"),
                            "epic": issue.get("epic"),
                            "assignees": issue.get("assignees", []),
                            "author": issue.get("author"),
                            "weight": issue.get("weight"),
                            "time_stats": issue.get("time_stats"),
                            "created_at": issue.get("created_at"),
                            "updated_at": issue.get("updated_at"),
                            "closed_at": issue.get("closed_at"),
                            "web_url": issue.get("web_url"),
                        })

                    # If we got fewer issues than per_page, we've reached the last page
                    if len(issues) < per_page:
                        break

                    current_page += 1
            else:
                # Fetch single page
                params["page"] = page
                issues = self.gl.http_get(f"/projects/{project_id}/issues", query_data=params)

                if not isinstance(issues, list):
                    return []

                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    result.append({
                        "id": issue.get("id"),
                        "iid": issue.get("iid"),
                        "title": issue.get("title"),
                        "description": issue.get("description"),
                        "state": issue.get("state"),
                        "labels": issue.get("labels", []),
                        "milestone": issue.get("milestone"),
                        "iteration": issue.get("iteration"),
                        "epic": issue.get("epic"),
                        "assignees": issue.get("assignees", []),
                        "author": issue.get("author"),
                        "weight": issue.get("weight"),
                        "time_stats": issue.get("time_stats"),
                        "created_at": issue.get("created_at"),
                        "updated_at": issue.get("updated_at"),
                        "closed_at": issue.get("closed_at"),
                        "web_url": issue.get("web_url"),
                    })

            return result
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch issues for project {project_id}")

    def get_issue(self, project_id: int, issue_iid: int) -> Dict[str, Any]:
        """
        Get a specific issue by IID.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID (not ID).

        Returns:
            Issue dictionary.
        """
        try:
            issue = self.gl.http_get(f"/projects/{project_id}/issues/{issue_iid}")
            if not isinstance(issue, dict):
                raise GitLabClientError(f"Failed to get issue {issue_iid} from project {project_id}: Unexpected response")

            return {
                "id": issue.get("id"),
                "iid": issue.get("iid"),
                "title": issue.get("title"),
                "description": issue.get("description"),
                "state": issue.get("state"),
                "labels": issue.get("labels", []),
                "milestone": issue.get("milestone"),
                "assignees": issue.get("assignees", []),
                "author": issue.get("author"),
                "weight": issue.get("weight"),
                "time_stats": issue.get("time_stats"),
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "closed_at": issue.get("closed_at"),
                "web_url": issue.get("web_url"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to get issue {issue_iid} from project {project_id}")

    def create_issue(
        self,
        project_id: int,
        title: str,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        weight: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a new issue.

        Args:
            project_id: The project ID.
            title: Issue title.
            description: Issue description (markdown).
            labels: List of label names.
            weight: Issue weight.

        Returns:
            Created issue dictionary.
        """
        try:
            data: Dict[str, Any] = {"title": title}
            if description is not None:
                data["description"] = description
            if labels:
                data["labels"] = ",".join(labels)
            if weight is not None:
                data["weight"] = weight

            issue = self.gl.http_post(f"/projects/{project_id}/issues", post_data=data)
            if not isinstance(issue, dict):
                raise GitLabClientError(f"Failed to create issue on project {project_id}: Unexpected response")

            return {
                "id": issue.get("id"),
                "iid": issue.get("iid"),
                "title": issue.get("title"),
                "description": issue.get("description"),
                "state": issue.get("state"),
                "labels": issue.get("labels", []),
                "weight": issue.get("weight"),
                "web_url": issue.get("web_url"),
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create issue on project {project_id}")

    def update_issue(
        self,
        project_id: int,
        issue_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        state_event: Optional[str] = None,
        weight: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            title: New title.
            description: New description.
            labels: New labels list.
            state_event: State change ('close' or 'reopen').
            weight: New weight.

        Returns:
            Updated issue dictionary.
        """
        try:
            data: Dict[str, Any] = {}
            if title is not None:
                data["title"] = title
            if description is not None:
                data["description"] = description
            if labels is not None:
                data["labels"] = ",".join(labels)
            if state_event is not None:
                data["state_event"] = state_event
            if weight is not None:
                data["weight"] = weight

            issue = self.gl.http_put(f"/projects/{project_id}/issues/{issue_iid}", post_data=data)
            if not isinstance(issue, dict):
                raise GitLabClientError(f"Failed to update issue {issue_iid} on project {project_id}: Unexpected response")

            return {
                "id": issue.get("id"),
                "iid": issue.get("iid"),
                "title": issue.get("title"),
                "description": issue.get("description"),
                "state": issue.get("state"),
                "labels": issue.get("labels", []),
                "weight": issue.get("weight"),
                "updated_at": issue.get("updated_at"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to update issue {issue_iid} on project {project_id}")

    # -------------------------------------------------------------------------
    # Label Operations (for issue mirroring)
    # -------------------------------------------------------------------------

    def get_project_labels(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all labels for a project.

        Args:
            project_id: The project ID.

        Returns:
            List of label dictionaries.
        """
        try:
            labels = self.gl.http_get(f"/projects/{project_id}/labels")
            if not isinstance(labels, list):
                return []

            return [
                {
                    "id": label.get("id"),
                    "name": label.get("name"),
                    "color": label.get("color"),
                    "description": label.get("description"),
                }
                for label in labels
                if isinstance(label, dict)
            ]
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch labels for project {project_id}")

    def create_label(
        self,
        project_id: int,
        name: str,
        color: str = "#428BCA",
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new project label.

        Args:
            project_id: The project ID.
            name: Label name.
            color: Label color (hex format).
            description: Label description.

        Returns:
            Created label dictionary.
        """
        try:
            data: Dict[str, Any] = {"name": name, "color": color}
            if description:
                data["description"] = description

            label = self.gl.http_post(f"/projects/{project_id}/labels", post_data=data)
            if not isinstance(label, dict):
                raise GitLabClientError(f"Failed to create label on project {project_id}: Unexpected response")

            return {
                "id": label.get("id"),
                "name": label.get("name"),
                "color": label.get("color"),
                "description": label.get("description"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create label '{name}' on project {project_id}")

    # -------------------------------------------------------------------------
    # Note/Comment Operations (for issue mirroring)
    # -------------------------------------------------------------------------

    def get_issue_notes(
        self,
        project_id: int,
        issue_iid: int,
        *,
        per_page: int = 100,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get comments/notes for an issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            per_page: Number of notes per page.
            page: Page number.

        Returns:
            List of note dictionaries.
        """
        try:
            params = {"per_page": per_page, "page": page}
            notes = self.gl.http_get(
                f"/projects/{project_id}/issues/{issue_iid}/notes",
                query_data=params
            )
            if not isinstance(notes, list):
                return []

            return [
                {
                    "id": note.get("id"),
                    "body": note.get("body"),
                    "author": note.get("author"),
                    "created_at": note.get("created_at"),
                    "updated_at": note.get("updated_at"),
                    "system": note.get("system", False),
                }
                for note in notes
                if isinstance(note, dict)
            ]
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to fetch notes for issue {issue_iid} on project {project_id}")

    def create_issue_note(
        self,
        project_id: int,
        issue_iid: int,
        body: str,
    ) -> Dict[str, Any]:
        """
        Create a comment on an issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            body: Comment body (markdown).

        Returns:
            Created note dictionary.
        """
        try:
            data = {"body": body}
            note = self.gl.http_post(
                f"/projects/{project_id}/issues/{issue_iid}/notes",
                post_data=data
            )
            if not isinstance(note, dict):
                raise GitLabClientError(
                    f"Failed to create note on issue {issue_iid} in project {project_id}: Unexpected response"
                )

            return {
                "id": note.get("id"),
                "body": note.get("body"),
                "author": note.get("author"),
                "created_at": note.get("created_at"),
                "updated_at": note.get("updated_at"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to create note on issue {issue_iid} in project {project_id}")

    def update_issue_note(
        self,
        project_id: int,
        issue_iid: int,
        note_id: int,
        body: str,
    ) -> Dict[str, Any]:
        """
        Update an existing comment on an issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            note_id: The note ID.
            body: New comment body (markdown).

        Returns:
            Updated note dictionary.
        """
        try:
            data = {"body": body}
            note = self.gl.http_put(
                f"/projects/{project_id}/issues/{issue_iid}/notes/{note_id}",
                post_data=data
            )
            if not isinstance(note, dict):
                raise GitLabClientError(
                    f"Failed to update note {note_id} on issue {issue_iid} in project {project_id}: Unexpected response"
                )

            return {
                "id": note.get("id"),
                "body": note.get("body"),
                "updated_at": note.get("updated_at"),
            }
        except Exception as e:
            _handle_gitlab_error(
                e, f"Failed to update note {note_id} on issue {issue_iid} in project {project_id}"
            )

    # -------------------------------------------------------------------------
    # Time Tracking Operations (for issue mirroring)
    # -------------------------------------------------------------------------

    def set_time_estimate(
        self,
        project_id: int,
        issue_iid: int,
        duration: str,
    ) -> Dict[str, Any]:
        """
        Set time estimate on an issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            duration: Duration string (e.g., '3h30m', '1d', '2w').

        Returns:
            Updated time stats.
        """
        try:
            data = {"duration": duration}
            result = self.gl.http_post(
                f"/projects/{project_id}/issues/{issue_iid}/time_estimate",
                post_data=data
            )
            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to set time estimate on issue {issue_iid} in project {project_id}: Unexpected response"
                )

            return {
                "time_estimate": result.get("time_estimate"),
                "total_time_spent": result.get("total_time_spent"),
                "human_time_estimate": result.get("human_time_estimate"),
                "human_total_time_spent": result.get("human_total_time_spent"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to set time estimate on issue {issue_iid} in project {project_id}")

    def reset_time_spent(
        self,
        project_id: int,
        issue_iid: int,
    ) -> Dict[str, Any]:
        """
        Reset time spent on an issue to 0.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.

        Returns:
            Updated time stats.
        """
        try:
            result = self.gl.http_post(
                f"/projects/{project_id}/issues/{issue_iid}/reset_time_spent"
            )
            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to reset time spent on issue {issue_iid} in project {project_id}: Unexpected response"
                )

            return {
                "time_estimate": result.get("time_estimate"),
                "total_time_spent": result.get("total_time_spent"),
                "human_time_estimate": result.get("human_time_estimate"),
                "human_total_time_spent": result.get("human_total_time_spent"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to reset time spent on issue {issue_iid} in project {project_id}")

    def add_time_spent(
        self,
        project_id: int,
        issue_iid: int,
        duration: str,
    ) -> Dict[str, Any]:
        """
        Add time spent to an issue.

        Args:
            project_id: The project ID.
            issue_iid: The issue IID.
            duration: Duration string (e.g., '3h30m', '1d', '2w').

        Returns:
            Updated time stats.
        """
        try:
            data = {"duration": duration}
            result = self.gl.http_post(
                f"/projects/{project_id}/issues/{issue_iid}/add_spent_time",
                post_data=data
            )
            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to add time spent on issue {issue_iid} in project {project_id}: Unexpected response"
                )

            return {
                "time_estimate": result.get("time_estimate"),
                "total_time_spent": result.get("total_time_spent"),
                "human_time_estimate": result.get("human_time_estimate"),
                "human_total_time_spent": result.get("human_total_time_spent"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to add time spent on issue {issue_iid} in project {project_id}")

    # -------------------------------------------------------------------------
    # File Upload Operations (for attachment mirroring)
    # -------------------------------------------------------------------------

    def upload_file(
        self,
        project_id: int,
        file_content: bytes,
        filename: str,
    ) -> Dict[str, Any]:
        """
        Upload a file to a project's uploads directory.

        Args:
            project_id: The project ID.
            file_content: File content as bytes.
            filename: Filename.

        Returns:
            Dict with 'url' (absolute URL) and 'markdown' (markdown link).
        """
        try:
            # GitLab expects multipart/form-data
            files = {"file": (filename, file_content)}
            result = self.gl.http_post(
                f"/projects/{project_id}/uploads",
                files=files
            )
            if not isinstance(result, dict):
                raise GitLabClientError(
                    f"Failed to upload file to project {project_id}: Unexpected response"
                )

            return {
                "url": result.get("url"),  # Relative URL
                "full_path": result.get("full_path"),
                "markdown": result.get("markdown"),
                "alt": result.get("alt"),
            }
        except Exception as e:
            _handle_gitlab_error(e, f"Failed to upload file to project {project_id}")
