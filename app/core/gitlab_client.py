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

    def create_pull_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        only_protected_branches: bool = False
    ) -> Dict[str, Any]:
        """Create a pull mirror for a project."""
        try:
            project = self.gl.projects.get(project_id)

            # GitLab API for pull mirrors
            mirror = project.remote_mirrors.create({
                "url": mirror_url,
                "enabled": enabled,
                "only_protected_branches": only_protected_branches
            })

            return {
                "id": mirror.id,
                "url": mirror.url,
                "enabled": mirror.enabled,
                "update_status": getattr(mirror, "update_status", "unknown"),
                "last_update_at": getattr(mirror, "last_update_at", None),
            }
        except Exception as e:
            raise Exception(f"Failed to create pull mirror: {str(e)}")

    def create_push_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled: bool = True,
        keep_divergent_refs: bool = False,
        only_protected_branches: bool = False
    ) -> Dict[str, Any]:
        """Create a push mirror for a project."""
        try:
            project = self.gl.projects.get(project_id)

            # For push mirrors, we use the import_url or mirror settings
            project.mirror = True
            project.import_url = mirror_url
            project.mirror_overwrites_diverged_branches = not keep_divergent_refs
            project.only_mirror_protected_branches = only_protected_branches
            project.save()

            return {
                "project_id": project.id,
                "mirror": project.mirror,
                "mirror_overwrites_diverged_branches": project.mirror_overwrites_diverged_branches,
            }
        except Exception as e:
            raise Exception(f"Failed to create push mirror: {str(e)}")

    def get_project_mirrors(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all mirrors for a project."""
        try:
            project = self.gl.projects.get(project_id)
            mirrors = []

            # Get remote mirrors (push mirrors)
            try:
                remote_mirrors = project.remote_mirrors.list()
                for m in remote_mirrors:
                    mirrors.append({
                        "id": m.id,
                        "url": m.url,
                        "enabled": m.enabled,
                        "update_status": getattr(m, "update_status", "unknown"),
                        "last_update_at": getattr(m, "last_update_at", None),
                        "last_successful_update_at": getattr(m, "last_successful_update_at", None),
                        "type": "push"
                    })
            except Exception:
                pass

            return mirrors
        except Exception as e:
            raise Exception(f"Failed to fetch project mirrors: {str(e)}")

    def trigger_mirror_update(self, project_id: int, mirror_id: int) -> bool:
        """Trigger an immediate update of a mirror."""
        try:
            project = self.gl.projects.get(project_id)
            mirror = project.remote_mirrors.get(mirror_id)
            mirror.update()
            return True
        except Exception as e:
            raise Exception(f"Failed to trigger mirror update: {str(e)}")

    def delete_mirror(self, project_id: int, mirror_id: int) -> bool:
        """Delete a mirror."""
        try:
            project = self.gl.projects.get(project_id)
            mirror = project.remote_mirrors.get(mirror_id)
            mirror.delete()
            return True
        except Exception as e:
            raise Exception(f"Failed to delete mirror: {str(e)}")

    def update_mirror(
        self,
        project_id: int,
        mirror_id: int,
        enabled: Optional[bool] = None,
        only_protected_branches: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Update mirror settings."""
        try:
            project = self.gl.projects.get(project_id)
            mirror = project.remote_mirrors.get(mirror_id)

            if enabled is not None:
                mirror.enabled = enabled
            if only_protected_branches is not None:
                mirror.only_protected_branches = only_protected_branches

            mirror.save()

            return {
                "id": mirror.id,
                "enabled": mirror.enabled,
                "only_protected_branches": getattr(mirror, "only_protected_branches", False),
            }
        except Exception as e:
            raise Exception(f"Failed to update mirror: {str(e)}")
