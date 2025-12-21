import gitlab
from typing import List, Dict, Any, Optional
from app.core.encryption import encryption


class GitLabClient:
    """Wrapper for GitLab API interactions."""

    def __init__(self, url: str, encrypted_token: str):
        """Initialize GitLab client with URL and encrypted token."""
        self.url = url
        self.token = encryption.decrypt(encrypted_token)
        self.gl = gitlab.Gitlab(url, private_token=self.token)

    def test_connection(self) -> bool:
        """Test if the connection to GitLab is working."""
        try:
            self.gl.auth()
            return True
        except Exception:
            return False

    def get_projects(self, search: Optional[str] = None, per_page: int = 100) -> List[Dict[str, Any]]:
        """Get list of projects from GitLab."""
        try:
            projects = self.gl.projects.list(
                search=search,
                get_all=True,
                per_page=per_page
            )
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
            raise Exception(f"Failed to fetch projects: {str(e)}")

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
            raise Exception(f"Failed to fetch project: {str(e)}")

    def get_groups(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of groups from GitLab."""
        try:
            groups = self.gl.groups.list(search=search, get_all=True)
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
            raise Exception(f"Failed to fetch groups: {str(e)}")

    def get_current_user(self) -> Dict[str, Any]:
        """Get the user associated with the API token."""
        try:
            u = self.gl.http_get("/user")
            if not isinstance(u, dict):
                raise Exception("Unexpected /user response")
            return {
                "id": u.get("id"),
                "username": u.get("username"),
                "name": u.get("name"),
            }
        except Exception as e:
            raise Exception(f"Failed to fetch current user: {str(e)}")

    def create_pull_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        only_protected_branches: bool = False,
        keep_divergent_refs: bool | None = None,
        trigger_builds: bool | None = None,
        mirror_branch_regex: str | None = None,
        mirror_user_id: int | None = None,
    ) -> Dict[str, Any]:
        """Create a pull mirror for a project (target pulls from source)."""
        try:
            return self._create_remote_mirror(
                project_id=project_id,
                mirror_url=mirror_url,
                enabled=enabled,
                only_protected_branches=only_protected_branches,
                keep_divergent_refs=keep_divergent_refs,
                trigger_builds=trigger_builds,
                mirror_branch_regex=mirror_branch_regex,
                mirror_user_id=mirror_user_id,
                mirror_direction="pull",
            )
        except Exception as e:
            raise Exception(f"Failed to create pull mirror: {str(e)}")

    def create_push_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        keep_divergent_refs: bool | None = None,
        only_protected_branches: bool = False,
        mirror_branch_regex: str | None = None,
        mirror_user_id: int | None = None,
    ) -> Dict[str, Any]:
        """Create a push mirror for a project (source pushes to target)."""
        try:
            return self._create_remote_mirror(
                project_id=project_id,
                mirror_url=mirror_url,
                enabled=enabled,
                only_protected_branches=only_protected_branches,
                keep_divergent_refs=keep_divergent_refs,
                mirror_branch_regex=mirror_branch_regex,
                mirror_user_id=mirror_user_id,
                mirror_direction="push",
            )
        except Exception as e:
            raise Exception(f"Failed to create push mirror: {str(e)}")

    def get_project_mirrors(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all mirrors for a project."""
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
                    "mirror_direction": m.get("mirror_direction"),
                    "only_protected_branches": m.get("only_protected_branches"),
                    "keep_divergent_refs": m.get("keep_divergent_refs"),
                    "trigger_builds": m.get("trigger_builds"),
                    "mirror_branch_regex": m.get("mirror_branch_regex"),
                    "mirror_user_id": m.get("mirror_user_id"),
                    "update_status": m.get("update_status"),
                    "last_update_at": m.get("last_update_at"),
                    "last_successful_update_at": m.get("last_successful_update_at"),
                })
            return out
        except Exception as e:
            raise Exception(f"Failed to fetch project mirrors: {str(e)}")

    def trigger_mirror_update(self, project_id: int, mirror_id: int) -> bool:
        """Trigger an immediate update of a mirror."""
        try:
            # GitLab: POST /projects/:id/remote_mirrors/:mirror_id/sync
            self.gl.http_post(f"/projects/{project_id}/remote_mirrors/{mirror_id}/sync")
            return True
        except Exception as e:
            raise Exception(f"Failed to trigger mirror update: {str(e)}")

    def delete_mirror(self, project_id: int, mirror_id: int) -> bool:
        """Delete a mirror."""
        try:
            self.gl.http_delete(f"/projects/{project_id}/remote_mirrors/{mirror_id}")
            return True
        except Exception as e:
            raise Exception(f"Failed to delete mirror: {str(e)}")

    def update_mirror(
        self,
        project_id: int,
        mirror_id: int,
        enabled: Optional[bool] = None,
        only_protected_branches: Optional[bool] = None,
        keep_divergent_refs: Optional[bool] = None,
        trigger_builds: Optional[bool] = None,
        mirror_branch_regex: Optional[str] = None,
        mirror_user_id: Optional[int] = None,
        mirror_direction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update mirror settings."""
        try:
            data: Dict[str, Any] = {}
            if enabled is not None:
                data["enabled"] = enabled
            if only_protected_branches is not None:
                data["only_protected_branches"] = only_protected_branches
            if keep_divergent_refs is not None:
                data["keep_divergent_refs"] = keep_divergent_refs
            if trigger_builds is not None:
                data["trigger_builds"] = trigger_builds
            if mirror_branch_regex is not None:
                data["mirror_branch_regex"] = mirror_branch_regex
            if mirror_user_id is not None:
                data["mirror_user_id"] = mirror_user_id

            if not data:
                return {"id": mirror_id}

            data = self._filter_remote_mirror_payload(mirror_direction, data)
            if not data:
                return {"id": mirror_id}

            return self.gl.http_put(
                f"/projects/{project_id}/remote_mirrors/{mirror_id}",
                post_data=data,
            )
        except Exception as e:
            raise Exception(f"Failed to update mirror: {str(e)}")

    def _create_remote_mirror(
        self,
        *,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        only_protected_branches: bool = False,
        keep_divergent_refs: bool | None = None,
        trigger_builds: bool | None = None,
        mirror_branch_regex: str | None = None,
        mirror_user_id: int | None = None,
        mirror_direction: str | None = None,
    ) -> Dict[str, Any]:
        """
        Create a remote mirror using a raw API call (bypasses python-gitlab's
        strict attribute whitelist so we can expose all GitLab UI settings).
        """
        data: Dict[str, Any] = {
            "url": mirror_url,
            "enabled": enabled,
            "only_protected_branches": only_protected_branches,
        }
        if keep_divergent_refs is not None:
            data["keep_divergent_refs"] = keep_divergent_refs
        if trigger_builds is not None:
            data["trigger_builds"] = trigger_builds
        if mirror_branch_regex is not None:
            data["mirror_branch_regex"] = mirror_branch_regex
        if mirror_user_id is not None:
            data["mirror_user_id"] = mirror_user_id
        if mirror_direction is not None:
            data["mirror_direction"] = mirror_direction

        data = self._filter_remote_mirror_payload(mirror_direction, data)
        return self.gl.http_post(f"/projects/{project_id}/remote_mirrors", post_data=data)

    @staticmethod
    def _filter_remote_mirror_payload(mirror_direction: str | None, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        GitLab supports different remote mirror settings depending on direction.

        We filter here so we don't send unsupported fields and cause 4xx errors.
        """
        if not mirror_direction:
            return data

        direction = mirror_direction.lower()
        # Conservative direction-aware support:
        # - Pull mirrors: all options supported.
        # - Push mirrors: GitLab typically doesn't support trigger_builds / branch regex / mirror user.
        if direction == "push":
            data.pop("trigger_builds", None)
            data.pop("mirror_branch_regex", None)
            data.pop("mirror_user_id", None)
        return data
