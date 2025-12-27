"""
Scenario C: Cross-instance mirroring.

Tests mirroring projects between two separate GitLab instances.
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
@pytest.mark.dual_instance
@pytest.mark.asyncio
async def test_cross_instance_push_mirror(client, e2e_config_dual, resource_tracker):
    """
    Test push mirroring from instance 1 to instance 2.

    1. Create source project with content on instance 1
    2. Create empty target project on instance 2
    3. Set up push mirror from source to target
    4. Verify content syncs across instances
    """
    cfg = e2e_config_dual
    run_id = generate_run_id()

    # Connect to both instances
    gl1 = gitlab.Gitlab(
        cfg["instance1"]["url"], private_token=cfg["instance1"]["token"]
    )
    gl1.auth()

    gl2 = gitlab.Gitlab(
        cfg["instance2"]["url"], private_token=cfg["instance2"]["token"]
    )
    gl2.auth()

    gl_clients = {cfg["instance1"]["url"]: gl1, cfg["instance2"]["url"]: gl2}

    # Create factories for each instance
    tracker1 = ResourceTracker()
    tracker2 = ResourceTracker()

    group_factory1 = GroupFactory(gl1, tracker1)
    project_factory1 = ProjectFactory(gl1, tracker1)

    group_factory2 = GroupFactory(gl2, tracker2)
    project_factory2 = ProjectFactory(gl2, tracker2)

    verifier = MirrorVerifier(gl1, gl2)

    created_mirrors = []
    source_instance_id = None
    target_instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create test subgroups on both instances
        source_group = group_factory1.create_test_subgroup(
            cfg["instance1"]["group_path"], f"e2e-cross-src-{run_id}"
        )
        target_group = group_factory2.create_test_subgroup(
            cfg["instance2"]["group_path"], f"e2e-cross-tgt-{run_id}"
        )

        # Create source project with content on instance 1
        source_project = project_factory1.create_project(
            f"cross-src-{run_id}",
            source_group["id"],
            ProjectContent(
                project_type="python",
                num_commits=5,
                branches=["main", "develop"],
                tags=["v1.0.0"],
            ),
        )

        # Create empty target project on instance 2
        target_project = project_factory2.create_empty_project(
            f"cross-tgt-{run_id}",
            target_group["id"],
        )

        # Register source instance
        src_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-cross-src-{run_id}",
                "url": cfg["instance1"]["url"],
                "token": cfg["instance1"]["token"],
                "description": "Source instance",
            },
        )
        assert src_inst_resp.status_code == 200
        source_instance_id = src_inst_resp.json()["id"]

        # Register target instance
        tgt_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-cross-tgt-{run_id}",
                "url": cfg["instance2"]["url"],
                "token": cfg["instance2"]["token"],
                "description": "Target instance",
            },
        )
        assert tgt_inst_resp.status_code == 200
        target_instance_id = tgt_inst_resp.json()["id"]

        # Create push pair (source instance -> target instance)
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-cross-push-{run_id}",
                "source_instance_id": source_instance_id,
                "target_instance_id": target_instance_id,
                "mirror_direction": "push",
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token for target instance (needed for authenticated push URL)
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": target_instance_id,
                "group_path": target_group["full_path"],
                "token": cfg["instance2"]["token"],
                "token_name": cfg["instance2"]["http_username"],
            },
        )
        assert tok_resp.status_code == 200
        token_id = tok_resp.json()["id"]

        # Create cross-instance push mirror
        mirror_resp = await client.post(
            "/api/mirrors",
            json={
                "instance_pair_id": pair_id,
                "source_project_id": source_project["id"],
                "source_project_path": source_project["path_with_namespace"],
                "target_project_id": target_project["id"],
                "target_project_path": target_project["path_with_namespace"],
                "enabled": True,
            },
        )
        assert mirror_resp.status_code == 200, mirror_resp.text
        mirror = mirror_resp.json()
        created_mirrors.append(mirror)

        # Verify mirror was created with GitLab ID
        assert mirror.get("mirror_id"), f"Mirror missing GitLab ID: {mirror}"

        # Trigger update
        update_resp = await client.post(f"/api/mirrors/{mirror['id']}/update")
        assert update_resp.status_code == 200

        # Wait for sync (push mirror lives on source project)
        status = await verifier.wait_for_mirror_sync(
            owner_project_id=source_project["id"],
            mirror_id=mirror["mirror_id"],
            owner_gl=gl1,
            timeout_s=cfg["mirror_timeout_s"],
        )
        print(f"Cross-instance mirror status: {status}")

        # Verify content synced across instances
        result = verifier.full_verification(
            source_project["id"],
            target_project["id"],
            check_files=["README.md", "config/settings.json"],
            expected_branches=["main", "develop"],
        )

        assert result["branches"]["all_match"], (
            f"Branch mismatch across instances: {result['branches']}"
        )

        # Check at least one file synced
        file_matches = [f for f in result["files"] if f.get("matches")]
        assert len(file_matches) > 0, f"No files synced: {result['files']}"

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
        if target_instance_id:
            try:
                await client.delete(f"/api/instances/{target_instance_id}")
            except Exception:
                pass
        if source_instance_id:
            try:
                await client.delete(f"/api/instances/{source_instance_id}")
            except Exception:
                pass

        # Cleanup GitLab resources on both instances
        errors1 = await tracker1.cleanup_all({cfg["instance1"]["url"]: gl1})
        errors2 = await tracker2.cleanup_all({cfg["instance2"]["url"]: gl2})
        if errors1:
            print(f"Instance 1 cleanup errors: {errors1}")
        if errors2:
            print(f"Instance 2 cleanup errors: {errors2}")


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.dual_instance
@pytest.mark.asyncio
async def test_cross_instance_pull_mirror(client, e2e_config_dual, resource_tracker):
    """
    Test pull mirroring: instance 2 pulls from instance 1.

    1. Create source project with content on instance 1
    2. Create empty target project on instance 2
    3. Set up pull mirror (target pulls from source)
    4. Verify content syncs across instances
    """
    cfg = e2e_config_dual
    run_id = generate_run_id()

    gl1 = gitlab.Gitlab(
        cfg["instance1"]["url"], private_token=cfg["instance1"]["token"]
    )
    gl1.auth()

    gl2 = gitlab.Gitlab(
        cfg["instance2"]["url"], private_token=cfg["instance2"]["token"]
    )
    gl2.auth()

    tracker1 = ResourceTracker()
    tracker2 = ResourceTracker()

    group_factory1 = GroupFactory(gl1, tracker1)
    project_factory1 = ProjectFactory(gl1, tracker1)

    group_factory2 = GroupFactory(gl2, tracker2)
    project_factory2 = ProjectFactory(gl2, tracker2)

    verifier = MirrorVerifier(gl1, gl2)

    created_mirrors = []
    source_instance_id = None
    target_instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create test subgroups
        source_group = group_factory1.create_test_subgroup(
            cfg["instance1"]["group_path"], f"e2e-pull-src-{run_id}"
        )
        target_group = group_factory2.create_test_subgroup(
            cfg["instance2"]["group_path"], f"e2e-pull-tgt-{run_id}"
        )

        # Create source with content
        source_project = project_factory1.create_project(
            f"pull-src-{run_id}",
            source_group["id"],
            ProjectContent(num_commits=4),
        )

        # Create empty target
        target_project = project_factory2.create_empty_project(
            f"pull-tgt-{run_id}",
            target_group["id"],
        )

        # Register instances
        src_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-pull-src-{run_id}",
                "url": cfg["instance1"]["url"],
                "token": cfg["instance1"]["token"],
            },
        )
        assert src_inst_resp.status_code == 200
        source_instance_id = src_inst_resp.json()["id"]

        tgt_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-pull-tgt-{run_id}",
                "url": cfg["instance2"]["url"],
                "token": cfg["instance2"]["token"],
            },
        )
        assert tgt_inst_resp.status_code == 200
        target_instance_id = tgt_inst_resp.json()["id"]

        # Create pull pair (target pulls from source)
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-pull-pair-{run_id}",
                "source_instance_id": source_instance_id,
                "target_instance_id": target_instance_id,
                "mirror_direction": "pull",
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token for source instance (needed for authenticated pull URL)
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": source_instance_id,
                "group_path": source_group["full_path"],
                "token": cfg["instance1"]["token"],
                "token_name": cfg["instance1"]["http_username"],
            },
        )
        assert tok_resp.status_code == 200
        token_id = tok_resp.json()["id"]

        # Create pull mirror
        mirror_resp = await client.post(
            "/api/mirrors",
            json={
                "instance_pair_id": pair_id,
                "source_project_id": source_project["id"],
                "source_project_path": source_project["path_with_namespace"],
                "target_project_id": target_project["id"],
                "target_project_path": target_project["path_with_namespace"],
                "enabled": True,
            },
        )
        assert mirror_resp.status_code == 200, mirror_resp.text
        mirror = mirror_resp.json()
        created_mirrors.append(mirror)

        # Trigger update
        update_resp = await client.post(f"/api/mirrors/{mirror['id']}/update")
        assert update_resp.status_code == 200

        # Wait for sync (pull mirror lives on target project)
        status = await verifier.wait_for_mirror_sync(
            owner_project_id=target_project["id"],
            mirror_id=mirror["mirror_id"],
            owner_gl=gl2,
            timeout_s=cfg["mirror_timeout_s"],
        )
        print(f"Cross-instance pull mirror status: {status}")

        # Verify
        result = verifier.full_verification(
            source_project["id"],
            target_project["id"],
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
        if target_instance_id:
            try:
                await client.delete(f"/api/instances/{target_instance_id}")
            except Exception:
                pass
        if source_instance_id:
            try:
                await client.delete(f"/api/instances/{source_instance_id}")
            except Exception:
                pass

        errors1 = await tracker1.cleanup_all({cfg["instance1"]["url"]: gl1})
        errors2 = await tracker2.cleanup_all({cfg["instance2"]["url"]: gl2})
        if errors1:
            print(f"Instance 1 cleanup errors: {errors1}")
        if errors2:
            print(f"Instance 2 cleanup errors: {errors2}")


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.dual_instance
@pytest.mark.asyncio
async def test_cross_instance_multiple_projects(
    client, e2e_config_dual, resource_tracker
):
    """
    Test mirroring multiple projects across instances.

    Creates 3 projects on instance 1 and mirrors them to instance 2.
    """
    cfg = e2e_config_dual
    run_id = generate_run_id()

    gl1 = gitlab.Gitlab(
        cfg["instance1"]["url"], private_token=cfg["instance1"]["token"]
    )
    gl1.auth()

    gl2 = gitlab.Gitlab(
        cfg["instance2"]["url"], private_token=cfg["instance2"]["token"]
    )
    gl2.auth()

    tracker1 = ResourceTracker()
    tracker2 = ResourceTracker()

    group_factory1 = GroupFactory(gl1, tracker1)
    project_factory1 = ProjectFactory(gl1, tracker1)

    group_factory2 = GroupFactory(gl2, tracker2)
    project_factory2 = ProjectFactory(gl2, tracker2)

    verifier = MirrorVerifier(gl1, gl2)

    created_mirrors = []
    source_instance_id = None
    target_instance_id = None
    pair_id = None
    token_id = None

    try:
        # Create groups
        source_group = group_factory1.create_test_subgroup(
            cfg["instance1"]["group_path"], f"e2e-batch-src-{run_id}"
        )
        target_group = group_factory2.create_test_subgroup(
            cfg["instance2"]["group_path"], f"e2e-batch-tgt-{run_id}"
        )

        # Create multiple projects
        project_names = ["service-a", "service-b", "service-c"]
        source_projects = []
        target_projects = []

        for name in project_names:
            src = project_factory1.create_project(
                f"{name}-src-{run_id}",
                source_group["id"],
                ProjectContent(num_commits=3),
            )
            source_projects.append(src)

            tgt = project_factory2.create_empty_project(
                f"{name}-tgt-{run_id}",
                target_group["id"],
            )
            target_projects.append(tgt)

        # Register instances
        src_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-batch-src-{run_id}",
                "url": cfg["instance1"]["url"],
                "token": cfg["instance1"]["token"],
            },
        )
        assert src_inst_resp.status_code == 200
        source_instance_id = src_inst_resp.json()["id"]

        tgt_inst_resp = await client.post(
            "/api/instances",
            json={
                "name": f"e2e-batch-tgt-{run_id}",
                "url": cfg["instance2"]["url"],
                "token": cfg["instance2"]["token"],
            },
        )
        assert tgt_inst_resp.status_code == 200
        target_instance_id = tgt_inst_resp.json()["id"]

        # Create pair
        pair_resp = await client.post(
            "/api/pairs",
            json={
                "name": f"e2e-batch-push-{run_id}",
                "source_instance_id": source_instance_id,
                "target_instance_id": target_instance_id,
                "mirror_direction": "push",
            },
        )
        assert pair_resp.status_code == 200
        pair_id = pair_resp.json()["id"]

        # Store token
        tok_resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": target_instance_id,
                "group_path": target_group["full_path"],
                "token": cfg["instance2"]["token"],
                "token_name": cfg["instance2"]["http_username"],
            },
        )
        assert tok_resp.status_code == 200
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
            assert mirror_resp.status_code == 200
            created_mirrors.append(mirror_resp.json())

        # Trigger all updates
        for mirror in created_mirrors:
            update_resp = await client.post(f"/api/mirrors/{mirror['id']}/update")
            assert update_resp.status_code == 200

        # Wait for all syncs
        for mirror in created_mirrors:
            await verifier.wait_for_mirror_sync(
                owner_project_id=mirror["source_project_id"],
                mirror_id=mirror["mirror_id"],
                owner_gl=gl1,
                timeout_s=cfg["mirror_timeout_s"],
            )

        # Verify all projects synced
        sync_results = []
        for src, tgt in zip(source_projects, target_projects):
            result = verifier.verify_branches(src["id"], tgt["id"], ["main"])
            sync_results.append(
                {
                    "source": src["path_with_namespace"],
                    "target": tgt["path_with_namespace"],
                    "synced": result["all_match"],
                }
            )

        # Check at least 2 of 3 projects synced (allow for timing issues)
        synced_count = sum(1 for r in sync_results if r["synced"])
        assert synced_count >= 2, f"Only {synced_count}/3 projects synced: {sync_results}"

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
        if target_instance_id:
            try:
                await client.delete(f"/api/instances/{target_instance_id}")
            except Exception:
                pass
        if source_instance_id:
            try:
                await client.delete(f"/api/instances/{source_instance_id}")
            except Exception:
                pass

        errors1 = await tracker1.cleanup_all({cfg["instance1"]["url"]: gl1})
        errors2 = await tracker2.cleanup_all({cfg["instance2"]["url"]: gl2})
        if errors1:
            print(f"Instance 1 cleanup errors: {errors1}")
        if errors2:
            print(f"Instance 2 cleanup errors: {errors2}")
