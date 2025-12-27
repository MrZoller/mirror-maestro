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
            raise Exception(f"Failed to create/update file: {str(e)}")

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
            raise Exception(f"Failed to get file: {str(e)}")

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
            return {
                "name": branch.name,
                "commit_sha": branch.commit["id"],
                "protected": branch.protected,
            }
        except Exception as e:
            raise Exception(f"Failed to create branch: {str(e)}")

    def get_branches(self, project_id: int) -> List[Dict[str, Any]]:
        """List all branches in a project."""
        try:
            project = self.gl.projects.get(project_id)
            branches = project.branches.list(get_all=True)
            return [
                {
                    "name": b.name,
                    "commit_sha": b.commit["id"],
                    "protected": b.protected,
                    "default": getattr(b, "default", False),
                }
                for b in branches
            ]
        except Exception as e:
            raise Exception(f"Failed to get branches: {str(e)}")

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
            raise Exception(f"Failed to protect branch: {str(e)}")

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
            return {
                "name": tag.name,
                "commit_sha": tag.commit["id"],
                "message": getattr(tag, "message", None),
            }
        except Exception as e:
            raise Exception(f"Failed to create tag: {str(e)}")

    def get_tags(self, project_id: int) -> List[Dict[str, Any]]:
        """List all tags in a project."""
        try:
            project = self.gl.projects.get(project_id)
            tags = project.tags.list(get_all=True)
            return [
                {
                    "name": t.name,
                    "commit_sha": t.commit["id"],
                    "message": getattr(t, "message", None),
                }
                for t in tags
            ]
        except Exception as e:
            raise Exception(f"Failed to get tags: {str(e)}")

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
            raise Exception(f"Failed to get commits: {str(e)}")

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
            raise Exception(f"Failed to get commit: {str(e)}")

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
            raise Exception(f"Failed to create commit: {str(e)}")

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
            raise Exception(f"Failed to create group: {str(e)}")

    def delete_group(self, group_id: int) -> bool:
        """Delete a group."""
        try:
            self.gl.groups.delete(group_id)
            return True
        except Exception as e:
            raise Exception(f"Failed to delete group: {str(e)}")

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
            raise Exception(f"Failed to get group: {str(e)}")

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
            raise Exception(f"Failed to create project: {str(e)}")

    def delete_project(self, project_id: int) -> bool:
        """Delete a project."""
        try:
            self.gl.projects.delete(project_id)
            return True
        except Exception as e:
            raise Exception(f"Failed to delete project: {str(e)}")
