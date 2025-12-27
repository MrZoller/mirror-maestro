"""
Scenario B: Multi-group hierarchy mirroring.

Creates subgroup structure with different group defaults at each level,
tests group token resolution and default inheritance.
"""

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
@pytest.mark.multi_group
@pytest.mark.asyncio
async def test_multi_group_hierarchy_with_defaults(
    client, e2e_config_single, resource_tracker
):
    """
    Test mirroring with group hierarchy and group-level defaults.

    Structure:
    - e2e-root/
      - frontend/ (trigger_builds=True)
        - web-app (source + target)
      - backend/ (only_protected_branches=True)
        - api-service (source + target)
      - shared/
        - common-lib (source + target)

    Verify group defaults are applied correctly at each level.
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
    group_default_ids = []

    try:
        # Create hierarchy
        groups = group_factory.create_hierarchy(
            cfg["group_path"],
            ["frontend", "backend", "shared"],
            run_id,
        )

        # Create projects in each subgroup
        projects_by_group = {}
        for group_name in ["frontend", "backend", "shared"]:
            group = groups[group_name]
            src = project_factory.create_project(
                f"src-{run_id}",
                group["id"],
                ProjectContent(num_commits=3),
            )
            tgt = project_factory.create_empty_project(
                f"tgt-{run_id}",
                group["id"],
            )
            projects_by_group[group_name] = {
                "source": src,
                "target": tgt,
            }

        # Register instance
        inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-hierarchy-{run_id}",
                "url": cfg["url"],
                "token": cfg["token"],
            },
        )
        assert inst_resp.status_code == 200
        instance_id = inst_resp.json()["id"]

        # Create pull pair with base defaults
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-hierarchy-pull-{run_id}",
                "source_instance_id": instance_id,
                "target_instance_id": instance_id,
                "mirror_direction": "pull",
                "mirror_trigger_builds": False,
                "only_mirror_protected_branches": False,
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token at root level (should apply to all subgroups)
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": instance_id,
                "group_path": groups["_root"]["full_path"],
                "token": cfg["token"],
                "token_name": cfg["http_username"],
            },
        )
        assert tok_resp.status_code == 200
        token_id = tok_resp.json()["id"]

        # Set group-level defaults for frontend: trigger_builds=True
        gd_frontend = await client.post(
            "/api/group-defaults",
            json={
                "instance_pair_id": pair_id,
                "group_path": groups["frontend"]["full_path"],
                "mirror_trigger_builds": True,
            },
        )
        assert gd_frontend.status_code == 200
        group_default_ids.append(gd_frontend.json()["id"])

        # Set group-level defaults for backend: only_protected_branches=True
        gd_backend = await client.post(
            "/api/group-defaults",
            json={
                "instance_pair_id": pair_id,
                "group_path": groups["backend"]["full_path"],
                "only_mirror_protected_branches": True,
            },
        )
        assert gd_backend.status_code == 200
        group_default_ids.append(gd_backend.json()["id"])

        # shared group uses pair defaults (no group defaults set)

        # Create mirrors and verify effective settings via preflight
        for group_name, projects in projects_by_group.items():
            src = projects["source"]
            tgt = projects["target"]

            # Preflight to check effective settings
            preflight_resp = await client.post(
                "/api/mirrors/preflight",
                json={
                    "instance_pair_id": pair_id,
                    "source_project_id": src["id"],
                    "source_project_path": src["path_with_namespace"],
                    "target_project_id": tgt["id"],
                    "target_project_path": tgt["path_with_namespace"],
                },
            )
            assert preflight_resp.status_code == 200
            preflight = preflight_resp.json()

            # Verify effective settings based on group
            if group_name == "frontend":
                # Frontend should have trigger_builds=True from group default
                assert preflight.get("effective_mirror_trigger_builds") is True, (
                    f"Frontend expected trigger_builds=True, got {preflight}"
                )
            elif group_name == "backend":
                # Backend should have only_protected=True from group default
                assert (
                    preflight.get("effective_only_mirror_protected_branches") is True
                ), f"Backend expected only_protected=True, got {preflight}"
            else:
                # Shared uses pair defaults
                assert preflight.get("effective_mirror_trigger_builds") is False
                assert (
                    preflight.get("effective_only_mirror_protected_branches") is False
                )

            # Create mirror
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

        # Wait for syncs
        timeout = cfg["mirror_timeout_s"]
        for mirror in created_mirrors:
            await verifier.wait_for_mirror_sync(
                owner_project_id=mirror["target_project_id"],
                mirror_id=mirror["mirror_id"],
                owner_gl=gl,
                timeout_s=timeout,
            )

        # Verify content synced for all groups
        for group_name, projects in projects_by_group.items():
            src = projects["source"]
            tgt = projects["target"]

            result = verifier.full_verification(
                src["id"],
                tgt["id"],
                check_files=["README.md"],
                expected_branches=["main"],
            )

            assert result["branches"]["all_match"], (
                f"Branch mismatch for {group_name}: {result['branches']}"
            )

    finally:
        for mirror in created_mirrors:
            try:
                await client.delete(f"/api/mirrors/{mirror['id']}")
            except Exception:
                pass

        for gd_id in group_default_ids:
            try:
                await client.delete(f"/api/group-defaults/{gd_id}")
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


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.multi_group
@pytest.mark.asyncio
async def test_token_inheritance_across_groups(
    client, e2e_config_single, resource_tracker
):
    """
    Test that tokens set at parent group level work for subgroups.

    1. Create root group with token
    2. Create subgroups without specific tokens
    3. Verify mirrors in subgroups can use the parent token
    """
    cfg = e2e_config_single
    run_id = generate_run_id()

    gl = gitlab.Gitlab(cfg["url"], private_token=cfg["token"])
    gl.auth()

    gl_clients = {cfg["url"]: gl}
    group_factory = GroupFactory(gl, resource_tracker)
    project_factory = ProjectFactory(gl, resource_tracker)

    created_mirrors = []
    instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create hierarchy with multiple levels
        root = group_factory.create_test_subgroup(
            cfg["group_path"], f"e2e-token-{run_id}"
        )
        child1 = group_factory.create_test_subgroup(root["full_path"], "child1")
        child2 = group_factory.create_test_subgroup(root["full_path"], "child2")

        # Create projects in child groups
        src1 = project_factory.create_project(
            f"src1-{run_id}",
            child1["id"],
            ProjectContent(num_commits=2),
        )
        tgt1 = project_factory.create_empty_project(f"tgt1-{run_id}", child1["id"])

        src2 = project_factory.create_project(
            f"src2-{run_id}",
            child2["id"],
            ProjectContent(num_commits=2),
        )
        tgt2 = project_factory.create_empty_project(f"tgt2-{run_id}", child2["id"])

        # Register instance
        inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-token-{run_id}",
                "url": cfg["url"],
                "token": cfg["token"],
            },
        )
        assert inst_resp.status_code == 200
        instance_id = inst_resp.json()["id"]

        # Create push pair
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-token-push-{run_id}",
                "source_instance_id": instance_id,
                "target_instance_id": instance_id,
                "mirror_direction": "push",
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token at ROOT level only
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": instance_id,
                "group_path": root["full_path"],
                "token": cfg["token"],
                "token_name": cfg["http_username"],
            },
        )
        assert tok_resp.status_code == 200
        token_id = tok_resp.json()["id"]

        # Create mirrors in child groups - should inherit root token
        for src, tgt in [(src1, tgt1), (src2, tgt2)]:
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
            # If token inheritance works, mirror creation should succeed
            assert mirror_resp.status_code == 200, (
                f"Mirror creation failed for {src['path_with_namespace']}: "
                f"{mirror_resp.text}"
            )
            created_mirrors.append(mirror_resp.json())

        # Verify mirrors were created with valid mirror_ids
        for mirror in created_mirrors:
            assert mirror.get("mirror_id"), f"Mirror missing GitLab ID: {mirror}"

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
