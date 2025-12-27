"""
E2E Test Helpers for GitLab Multi-Project Mirroring Tests.

This module provides factory classes and utilities for creating realistic
test projects with files, branches, tags, and commit history.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import gitlab


def generate_run_id() -> str:
    """Generate a unique run ID for test isolation."""
    return uuid.uuid4().hex[:10]


def should_keep_resources() -> bool:
    """Check if E2E_KEEP_RESOURCES is set to skip cleanup."""
    import os
    return (os.getenv("E2E_KEEP_RESOURCES") or "").lower() in {"1", "true", "yes", "on"}


@dataclass
class ProjectContent:
    """Defines the content structure for a test project."""

    project_type: str = "python"  # python, javascript, go
    num_commits: int = 5
    branches: list[str] = field(default_factory=lambda: ["main", "develop"])
    feature_branches: list[str] = field(default_factory=lambda: ["feature/new-feature"])
    tags: list[str] = field(default_factory=lambda: ["v1.0.0", "v1.1.0"])
    include_gitignore: bool = True
    include_readme: bool = True

    @property
    def all_branches(self) -> list[str]:
        return self.branches + self.feature_branches


@dataclass
class CreatedResource:
    """Tracks a created resource for cleanup."""

    resource_type: str  # "project", "group", "mirror"
    resource_id: int
    instance_url: str
    extra: dict[str, Any] = field(default_factory=dict)


class ResourceTracker:
    """Tracks all created resources for guaranteed cleanup."""

    def __init__(self):
        self._resources: list[CreatedResource] = []

    def track(self, resource_type: str, resource_id: int, instance_url: str, **extra):
        """Track a resource for later cleanup."""
        self._resources.append(
            CreatedResource(
                resource_type=resource_type,
                resource_id=resource_id,
                instance_url=instance_url,
                extra=extra,
            )
        )

    def get_resources(
        self, resource_type: Optional[str] = None
    ) -> list[CreatedResource]:
        """Get tracked resources, optionally filtered by type."""
        if resource_type:
            return [r for r in self._resources if r.resource_type == resource_type]
        return list(self._resources)

    async def cleanup_all(self, gl_clients: dict[str, gitlab.Gitlab]) -> list[str]:
        """
        Clean up all tracked resources in reverse order.
        Returns list of cleanup errors (if any).

        If E2E_KEEP_RESOURCES=1 is set, skips cleanup and prints resource info instead.
        """
        if should_keep_resources():
            print("\n" + "=" * 60)
            print("E2E_KEEP_RESOURCES=1 is set - SKIPPING CLEANUP")
            print("The following resources were kept for manual inspection:")
            print("=" * 60)
            for resource in self._resources:
                extra_info = ""
                if resource.extra:
                    extra_info = f" ({resource.extra})"
                print(f"  [{resource.resource_type}] ID={resource.resource_id} @ {resource.instance_url}{extra_info}")
            print("=" * 60)
            print("To clean up manually, delete in this order: projects, then groups")
            print("=" * 60 + "\n")
            return []

        errors = []
        # Reverse order: projects before groups
        for resource in reversed(self._resources):
            try:
                gl = gl_clients.get(resource.instance_url)
                if not gl:
                    errors.append(f"No client for {resource.instance_url}")
                    continue

                if resource.resource_type == "project":
                    gl.projects.delete(resource.resource_id)
                elif resource.resource_type == "group":
                    gl.groups.delete(resource.resource_id)
                # Add small delay to avoid rate limiting
                await asyncio.sleep(0.2)
            except Exception as e:
                errors.append(
                    f"Failed to delete {resource.resource_type} {resource.resource_id}: {e}"
                )

        self._resources.clear()
        return errors


class ProjectFactory:
    """Creates test projects with realistic content."""

    # Template files by project type
    TEMPLATES = {
        "python": {
            "src/main.py": '''"""Main application module."""


def main():
    """Entry point for the application."""
    print("Hello, Mirror Maestro!")
    return 0


if __name__ == "__main__":
    main()
''',
            "src/utils.py": '''"""Utility functions."""


def helper_function(value: str) -> str:
    """A helpful utility function."""
    return value.upper()
''',
            "tests/test_main.py": '''"""Tests for main module."""
import pytest


def test_main():
    """Test main function."""
    from src.main import main

    assert main() == 0
''',
            "config/settings.json": '{"app_name": "test-project", "version": "1.0.0"}',
            ".gitignore": """__pycache__/
*.pyc
.pytest_cache/
.venv/
dist/
*.egg-info/
""",
        },
        "javascript": {
            "src/index.js": '''/**
 * Main entry point
 */
function main() {
    console.log("Hello, Mirror Maestro!");
    return 0;
}

module.exports = { main };
''',
            "src/utils.js": '''/**
 * Utility functions
 */
function helperFunction(value) {
    return value.toUpperCase();
}

module.exports = { helperFunction };
''',
            "tests/index.test.js": '''const { main } = require("../src/index");

describe("main", () => {
    it("should return 0", () => {
        expect(main()).toBe(0);
    });
});
''',
            "config/settings.json": '{"appName": "test-project", "version": "1.0.0"}',
            ".gitignore": """node_modules/
dist/
*.log
.env
coverage/
""",
        },
        "go": {
            "main.go": '''package main

import "fmt"

func main() {
    fmt.Println("Hello, Mirror Maestro!")
}
''',
            "utils/helper.go": '''package utils

// HelperFunction does something helpful.
func HelperFunction(value string) string {
    return value
}
''',
            "main_test.go": '''package main

import "testing"

func TestMain(t *testing.T) {
    // Basic test
    t.Log("Test passed")
}
''',
            "config/settings.json": '{"app_name": "test-project", "version": "1.0.0"}',
            ".gitignore": """bin/
*.exe
vendor/
""",
        },
    }

    COMMIT_MESSAGES = [
        "Initial project structure",
        "Add main application logic",
        "Add utility functions",
        "Add test coverage",
        "Add configuration files",
        "Refactor for better maintainability",
        "Fix edge case in utils",
        "Update documentation",
        "Add error handling",
        "Performance optimization",
    ]

    def __init__(self, gl: gitlab.Gitlab, tracker: ResourceTracker):
        self.gl = gl
        self.tracker = tracker
        self.instance_url = gl.url

    def create_project(
        self,
        name: str,
        namespace_id: int,
        content: Optional[ProjectContent] = None,
    ) -> dict[str, Any]:
        """
        Create a project with realistic content structure.

        Returns project info dict with id, path, etc.
        """
        content = content or ProjectContent()

        # Create empty project
        project = self.gl.projects.create(
            {
                "name": name,
                "path": name,
                "namespace_id": namespace_id,
                "visibility": "private",
                "initialize_with_readme": False,
            }
        )

        self.tracker.track("project", project.id, self.instance_url, name=name)

        # Build up content with commits
        self._populate_project(project, content)

        return {
            "id": project.id,
            "name": project.name,
            "path": project.path,
            "path_with_namespace": project.path_with_namespace,
            "http_url_to_repo": project.http_url_to_repo,
        }

    def create_empty_project(
        self,
        name: str,
        namespace_id: int,
    ) -> dict[str, Any]:
        """Create an empty project (for use as mirror target)."""
        project = self.gl.projects.create(
            {
                "name": name,
                "path": name,
                "namespace_id": namespace_id,
                "visibility": "private",
                "initialize_with_readme": False,
            }
        )

        self.tracker.track("project", project.id, self.instance_url, name=name)

        return {
            "id": project.id,
            "name": project.name,
            "path": project.path,
            "path_with_namespace": project.path_with_namespace,
            "http_url_to_repo": project.http_url_to_repo,
        }

    def _populate_project(self, project, content: ProjectContent):
        """Populate project with files, branches, and tags."""
        templates = self.TEMPLATES.get(content.project_type, self.TEMPLATES["python"])

        # Create initial files on main branch
        files_to_commit = []

        # Add README
        if content.include_readme:
            files_to_commit.append(
                {
                    "action": "create",
                    "file_path": "README.md",
                    "content": f"# {project.name}\n\nTest project for Mirror Maestro E2E tests.\n",
                }
            )

        # Add template files
        for file_path, file_content in templates.items():
            if file_path == ".gitignore" and not content.include_gitignore:
                continue
            files_to_commit.append(
                {
                    "action": "create",
                    "file_path": file_path,
                    "content": file_content,
                }
            )

        # Create initial commit with all files
        project.commits.create(
            {
                "branch": "main",
                "commit_message": self.COMMIT_MESSAGES[0],
                "actions": files_to_commit,
            }
        )

        # Add more commits to simulate history
        for i in range(1, min(content.num_commits, len(self.COMMIT_MESSAGES))):
            # Add a new file or modify existing
            project.commits.create(
                {
                    "branch": "main",
                    "commit_message": self.COMMIT_MESSAGES[i],
                    "actions": [
                        {
                            "action": "create",
                            "file_path": f"docs/update-{i}.md",
                            "content": f"# Update {i}\n\nCommit history for testing.\n",
                        }
                    ],
                }
            )

        # Create branches
        for branch in content.branches:
            if branch != "main":
                try:
                    project.branches.create({"branch": branch, "ref": "main"})
                except Exception:
                    pass  # Branch might already exist

        for branch in content.feature_branches:
            try:
                base_ref = "develop" if "develop" in content.branches else "main"
                project.branches.create({"branch": branch, "ref": base_ref})
                # Add a commit to feature branch
                project.commits.create(
                    {
                        "branch": branch,
                        "commit_message": f"Work in progress on {branch}",
                        "actions": [
                            {
                                "action": "create",
                                "file_path": f"features/{branch.replace('/', '-')}.md",
                                "content": f"# Feature: {branch}\n\nWork in progress.\n",
                            }
                        ],
                    }
                )
            except Exception:
                pass

        # Create tags
        for tag in content.tags:
            try:
                project.tags.create(
                    {
                        "tag_name": tag,
                        "ref": "main",
                        "message": f"Release {tag}",
                    }
                )
            except Exception:
                pass


class GroupFactory:
    """Creates temporary subgroup hierarchies for testing."""

    def __init__(self, gl: gitlab.Gitlab, tracker: ResourceTracker):
        self.gl = gl
        self.tracker = tracker
        self.instance_url = gl.url

    def create_test_subgroup(
        self,
        parent_group_path: str,
        subgroup_name: str,
        visibility: str = "private",
    ) -> dict[str, Any]:
        """
        Create a subgroup under the specified parent group.

        Returns group info dict with id, path, full_path.
        """
        parent = self.gl.groups.get(parent_group_path)

        subgroup = self.gl.groups.create(
            {
                "name": subgroup_name,
                "path": subgroup_name,
                "parent_id": parent.id,
                "visibility": visibility,
            }
        )

        self.tracker.track("group", subgroup.id, self.instance_url, name=subgroup_name)

        return {
            "id": subgroup.id,
            "name": subgroup.name,
            "path": subgroup.path,
            "full_path": subgroup.full_path,
        }

    def create_hierarchy(
        self,
        parent_group_path: str,
        subgroup_names: list[str],
        run_id: str,
    ) -> dict[str, dict[str, Any]]:
        """
        Create a hierarchy of subgroups.

        Args:
            parent_group_path: The base group to create under
            subgroup_names: List of subgroup names to create under root
            run_id: Unique run identifier for naming

        Returns:
            Dict mapping subgroup names to their info dicts (includes "_root")
        """
        result = {}

        # First create the root test group
        root_name = f"e2e-test-{run_id}"
        root = self.create_test_subgroup(parent_group_path, root_name)
        result["_root"] = root

        # Create child subgroups
        for subgroup_name in subgroup_names:
            child = self.create_test_subgroup(root["full_path"], subgroup_name)
            result[subgroup_name] = child

        return result


class MirrorVerifier:
    """Deep verification of mirror synchronization."""

    def __init__(self, source_gl: gitlab.Gitlab, target_gl: gitlab.Gitlab):
        self.source_gl = source_gl
        self.target_gl = target_gl

    async def wait_for_mirror_sync(
        self,
        owner_project_id: int,
        mirror_id: int,
        owner_gl: gitlab.Gitlab,
        timeout_s: float = 120.0,
        poll_interval_s: float = 5.0,
    ) -> dict[str, Any]:
        """
        Wait for a mirror to complete synchronization.

        Returns the mirror status dict.
        """
        deadline = time.monotonic() + timeout_s
        last_status = None

        while time.monotonic() < deadline:
            try:
                project = owner_gl.projects.get(owner_project_id)
                rm = project.remote_mirrors.get(mirror_id)

                last_status = {
                    "enabled": getattr(rm, "enabled", None),
                    "update_status": getattr(rm, "update_status", None),
                    "last_update_at": getattr(rm, "last_update_at", None),
                    "last_successful_update_at": getattr(
                        rm, "last_successful_update_at", None
                    ),
                    "last_error": getattr(rm, "last_error", None),
                }

                # Check for completion or failure
                status = last_status.get("update_status", "")
                if status in ("finished", "failed"):
                    return last_status
                if last_status.get("last_successful_update_at"):
                    return last_status
            except Exception:
                pass

            await asyncio.sleep(poll_interval_s)

        return last_status or {"error": "timeout"}

    def verify_branches(
        self,
        source_project_id: int,
        target_project_id: int,
        expected_branches: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Verify that branches match between source and target.

        Returns verification result with matches and mismatches.
        """
        source_project = self.source_gl.projects.get(source_project_id)
        target_project = self.target_gl.projects.get(target_project_id)

        source_branches = {
            b.name: b.commit["id"] for b in source_project.branches.list(get_all=True)
        }
        target_branches = {
            b.name: b.commit["id"] for b in target_project.branches.list(get_all=True)
        }

        if expected_branches:
            source_branches = {
                k: v for k, v in source_branches.items() if k in expected_branches
            }

        matches = []
        mismatches = []
        missing = []

        for branch, source_sha in source_branches.items():
            if branch in target_branches:
                if source_sha == target_branches[branch]:
                    matches.append({"branch": branch, "sha": source_sha})
                else:
                    mismatches.append(
                        {
                            "branch": branch,
                            "source_sha": source_sha,
                            "target_sha": target_branches[branch],
                        }
                    )
            else:
                missing.append({"branch": branch, "source_sha": source_sha})

        return {
            "matches": matches,
            "mismatches": mismatches,
            "missing": missing,
            "all_match": len(mismatches) == 0 and len(missing) == 0,
        }

    def verify_tags(
        self,
        source_project_id: int,
        target_project_id: int,
    ) -> dict[str, Any]:
        """Verify that tags match between source and target."""
        source_project = self.source_gl.projects.get(source_project_id)
        target_project = self.target_gl.projects.get(target_project_id)

        source_tags = {
            t.name: t.commit["id"] for t in source_project.tags.list(get_all=True)
        }
        target_tags = {
            t.name: t.commit["id"] for t in target_project.tags.list(get_all=True)
        }

        matches = []
        mismatches = []
        missing = []

        for tag, source_sha in source_tags.items():
            if tag in target_tags:
                if source_sha == target_tags[tag]:
                    matches.append({"tag": tag, "sha": source_sha})
                else:
                    mismatches.append(
                        {
                            "tag": tag,
                            "source_sha": source_sha,
                            "target_sha": target_tags[tag],
                        }
                    )
            else:
                missing.append({"tag": tag, "source_sha": source_sha})

        return {
            "matches": matches,
            "mismatches": mismatches,
            "missing": missing,
            "all_match": len(mismatches) == 0 and len(missing) == 0,
        }

    def verify_file_content(
        self,
        source_project_id: int,
        target_project_id: int,
        file_path: str,
        ref: str = "main",
    ) -> dict[str, Any]:
        """Verify that a specific file matches between source and target."""
        try:
            source_project = self.source_gl.projects.get(source_project_id)
            target_project = self.target_gl.projects.get(target_project_id)

            source_file = source_project.files.get(file_path=file_path, ref=ref)
            target_file = target_project.files.get(file_path=file_path, ref=ref)

            source_content = base64.b64decode(source_file.content).decode("utf-8")
            target_content = base64.b64decode(target_file.content).decode("utf-8")

            return {
                "file_path": file_path,
                "ref": ref,
                "matches": source_content == target_content,
                "source_size": len(source_content),
                "target_size": len(target_content),
            }
        except Exception as e:
            return {
                "file_path": file_path,
                "ref": ref,
                "matches": False,
                "error": str(e),
            }

    def full_verification(
        self,
        source_project_id: int,
        target_project_id: int,
        check_files: Optional[list[str]] = None,
        expected_branches: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Perform full verification of mirror sync.

        Returns comprehensive verification report.
        """
        branch_result = self.verify_branches(
            source_project_id, target_project_id, expected_branches
        )
        tag_result = self.verify_tags(source_project_id, target_project_id)

        file_results = []
        if check_files:
            for file_path in check_files:
                file_results.append(
                    self.verify_file_content(
                        source_project_id, target_project_id, file_path
                    )
                )

        all_files_match = (
            all(f.get("matches", False) for f in file_results) if file_results else True
        )

        return {
            "branches": branch_result,
            "tags": tag_result,
            "files": file_results,
            "overall_success": (
                branch_result["all_match"] and tag_result["all_match"] and all_files_match
            ),
        }
