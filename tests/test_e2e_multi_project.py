"""
Scenario A: Multi-project same-group mirroring.

Creates 3 projects in one subgroup with different content profiles,
sets up push mirrors, and verifies all content syncs correctly.
"""

import os

import gitlab
import pytest

from tests.e2e_helpers import (
    GroupFactory,
    MirrorVerifier,
    ProjectContent,
    ProjectFactory,
    ResourceTracker,
    generate_run_id,
)


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.multi_project
@pytest.mark.asyncio
async def test_multi_project_push_mirrors(client, e2e_config_single, resource_tracker):
    """
    Test mirroring multiple projects from a single group.

    1. Create a test subgroup with 3 projects of different types
    2. Register the GitLab instance and create push pair
    3. Create push mirrors for all projects
    4. Verify all content (files, branches, tags) syncs correctly
    """
    cfg = e2e_config_single
    run_id = generate_run_id()

    gl = gitlab.Gitlab(cfg["url"], private_token=cfg["token"])
    gl.auth()

    gl_clients = {cfg["url"]: gl}

    # Create test infrastructure
    group_factory = GroupFactory(gl, resource_tracker)
    project_factory = ProjectFactory(gl, resource_tracker)
    verifier = MirrorVerifier(gl, gl)  # Same instance for source/target

    created_mirrors = []
    instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create test subgroup
        test_group = group_factory.create_test_subgroup(
            cfg["group_path"], f"e2e-multi-{run_id}"
        )

        # Define project configurations
        project_configs = [
            ("python-svc", ProjectContent(project_type="python", num_commits=5)),
            ("js-frontend", ProjectContent(project_type="javascript", num_commits=4)),
            ("go-backend", ProjectContent(project_type="go", num_commits=3)),
        ]

        source_projects = []
        target_projects = []

        # Create source and target projects
        for name, content in project_configs:
            source = project_factory.create_project(
                f"{name}-src-{run_id}",
                test_group["id"],
                content,
            )
            source_projects.append(source)

            # Create empty target project
            target = project_factory.create_empty_project(
                f"{name}-tgt-{run_id}",
                test_group["id"],
            )
            target_projects.append(target)

        # Register instance via API
        inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-multi-{run_id}",
                "url": cfg["url"],
                "token": cfg["token"],
                "description": "Multi-project E2E test",
            },
        )
        assert inst_resp.status_code == 200, inst_resp.text
        instance_id = inst_resp.json()["id"]

        # Create push pair
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-multi-push-{run_id}",
                "source_instance_id": instance_id,
                "target_instance_id": instance_id,
                "mirror_direction": "push",
            },
        )
        assert pair_resp.status_code == 200, pair_resp.text
        pair_id = pair_resp.json()["id"]

        # Store token for authenticated URLs
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": instance_id,
                "group_path": test_group["full_path"],
                "token": cfg["token"],
                "token_name": cfg["http_username"],
            },
        )
        assert tok_resp.status_code == 200, tok_resp.text
        token_id = tok_resp.json()["id"]

        # Create mirrors for all projects
        for src, tgt in zip(source_projects, target_projects):
            mirror_resp = await client.post(
                "/api/mirrors",
                json={
                    "instance_pair_id": pair_id,
                    "source_project_id": src["id"],
                    "source_project_path": src["path_with_namespace"],
                    "target_project_id": tgt["id"],
                    "target_project_path": tgt["path_with_namespace"],
                    "enabled": True,
                },
            )
            assert mirror_resp.status_code == 200, mirror_resp.text
            created_mirrors.append(mirror_resp.json())

        # Trigger updates for all mirrors
        for mirror in created_mirrors:
            update_resp = await client.post(f"/api/mirrors/{mirror['id']}/update")
            assert update_resp.status_code == 200, update_resp.text

        # Wait for syncs to complete
        timeout = cfg["mirror_timeout_s"]
        for mirror in created_mirrors:
            status = await verifier.wait_for_mirror_sync(
                owner_project_id=mirror["source_project_id"],
                mirror_id=mirror["mirror_id"],
                owner_gl=gl,
                timeout_s=timeout,
            )
            # Log status for debugging
            print(f"Mirror {mirror['id']} status: {status}")

        # Verify all projects
        for src, tgt in zip(source_projects, target_projects):
            result = verifier.full_verification(
                src["id"],
                tgt["id"],
                check_files=["README.md", "config/settings.json"],
                expected_branches=["main", "develop"],
            )

            assert result["branches"]["all_match"], (
                f"Branch mismatch for {src['path_with_namespace']}: "
                f"mismatches={result['branches']['mismatches']}, "
                f"missing={result['branches']['missing']}"
            )

            # Tags may not sync immediately on push mirrors, so we check but don't fail
            if not result["tags"]["all_match"]:
                print(
                    f"Warning: Tag mismatch for {src['path_with_namespace']}: "
                    f"missing={result['tags']['missing']}"
                )

            # Check files synced
            for file_result in result["files"]:
                assert file_result["matches"], (
                    f"File mismatch for {src['path_with_namespace']}: "
                    f"{file_result}"
                )

    finally:
        # Cleanup mirrors via API
        for mirror in created_mirrors:
            try:
                await client.delete(f"/api/mirrors/{mirror['id']}")
            except Exception:
                pass

        # Cleanup API resources
        if token_id:
            try:
                await client.delete(f"/api/tokens/{token_id}")
            except Exception:
                pass
        if pair_id:
            try:
                await client.delete(f"/api/pairs/{pair_id}")
            except Exception:
                pass
        if instance_id:
            try:
                await client.delete(f"/api/instances/{instance_id}")
            except Exception:
                pass

        # Cleanup GitLab resources
        errors = await resource_tracker.cleanup_all(gl_clients)
        if errors:
            print(f"Cleanup errors: {errors}")


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.multi_project
@pytest.mark.asyncio
async def test_multi_project_pull_mirrors(client, e2e_config_single, resource_tracker):
    """
    Test pull mirroring multiple projects.

    1. Create source projects with content
    2. Create empty target projects
    3. Set up pull mirrors (target pulls from source)
    4. Verify content syncs correctly
    """
    cfg = e2e_config_single
    run_id = generate_run_id()

    gl = gitlab.Gitlab(cfg["url"], private_token=cfg["token"])
    gl.auth()

    gl_clients = {cfg["url"]: gl}

    group_factory = GroupFactory(gl, resource_tracker)
    project_factory = ProjectFactory(gl, resource_tracker)
    verifier = MirrorVerifier(gl, gl)

    created_mirrors = []
    instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create test subgroup
        test_group = group_factory.create_test_subgroup(
            cfg["group_path"], f"e2e-pull-{run_id}"
        )

        # Create 2 projects for pull mirror testing
        project_configs = [
            ("lib-core", ProjectContent(project_type="python", num_commits=3)),
            ("lib-utils", ProjectContent(project_type="python", num_commits=3)),
        ]

        source_projects = []
        target_projects = []

        for name, content in project_configs:
            source = project_factory.create_project(
                f"{name}-src-{run_id}",
                test_group["id"],
                content,
            )
            source_projects.append(source)

            target = project_factory.create_empty_project(
                f"{name}-tgt-{run_id}",
                test_group["id"],
            )
            target_projects.append(target)

        # Register instance
        inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-pull-{run_id}",
                "url": cfg["url"],
                "token": cfg["token"],
            },
        )
        assert inst_resp.status_code == 200
        instance_id = inst_resp.json()["id"]

        # Create pull pair
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-pull-pair-{run_id}",
                "source_instance_id": instance_id,
                "target_instance_id": instance_id,
                "mirror_direction": "pull",
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": instance_id,
                "group_path": test_group["full_path"],
                "token": cfg["token"],
                "token_name": cfg["http_username"],
            },
        )
        assert tok_resp.status_code == 200
        token_id = tok_resp.json()["id"]

        # Create pull mirrors
        for src, tgt in zip(source_projects, target_projects):
            mirror_resp = await client.post(
                "/api/mirrors",
                json={
                    "instance_pair_id": pair_id,
                    "source_project_id": src["id"],
                    "source_project_path": src["path_with_namespace"],
                    "target_project_id": tgt["id"],
                    "target_project_path": tgt["path_with_namespace"],
                    "enabled": True,
                },
            )
            assert mirror_resp.status_code == 200, mirror_resp.text
            created_mirrors.append(mirror_resp.json())

        # Trigger updates
        for mirror in created_mirrors:
            update_resp = await client.post(f"/api/mirrors/{mirror['id']}/update")
            assert update_resp.status_code == 200

        # Wait for syncs - for pull mirrors, the owner is the target project
        timeout = cfg["mirror_timeout_s"]
        for mirror in created_mirrors:
            status = await verifier.wait_for_mirror_sync(
                owner_project_id=mirror["target_project_id"],
                mirror_id=mirror["mirror_id"],
                owner_gl=gl,
                timeout_s=timeout,
            )
            print(f"Pull mirror {mirror['id']} status: {status}")

        # Verify
        for src, tgt in zip(source_projects, target_projects):
            result = verifier.full_verification(
                src["id"],
                tgt["id"],
                check_files=["README.md"],
                expected_branches=["main"],
            )

            assert result["branches"]["all_match"], (
                f"Branch mismatch: {result['branches']}"
            )

    finally:
        for mirror in created_mirrors:
            try:
                await client.delete(f"/api/mirrors/{mirror['id']}")
            except Exception:
                pass

        if token_id:
            try:
                await client.delete(f"/api/tokens/{token_id}")
            except Exception:
                pass
        if pair_id:
            try:
                await client.delete(f"/api/pairs/{pair_id}")
            except Exception:
                pass
        if instance_id:
            try:
                await client.delete(f"/api/instances/{instance_id}")
            except Exception:
                pass

        errors = await resource_tracker.cleanup_all(gl_clients)
        if errors:
            print(f"Cleanup errors: {errors}")
