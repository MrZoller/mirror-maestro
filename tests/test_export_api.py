import json
from unittest.mock import MagicMock, patch

import pytest

from app.models import GitLabInstance, InstancePair, Mirror


async def seed_instance(session_maker, *, name: str) -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url="https://x", encrypted_token="enc:t", description="")
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


async def seed_pair(session_maker, *, name: str, src_id: int, tgt_id: int) -> int:
    async with session_maker() as s:
        pair = InstancePair(name=name, source_instance_id=src_id, target_instance_id=tgt_id)
        s.add(pair)
        await s.commit()
        await s.refresh(pair)
        return pair.id


# Counter to generate unique project IDs for tests
_project_id_counter = 0

def _get_unique_project_id() -> int:
    """Generate unique project IDs for test mirrors to avoid constraint violations."""
    global _project_id_counter
    _project_id_counter += 1
    return _project_id_counter

async def seed_mirror(
    session_maker,
    *,
    pair_id: int,
    src_path: str,
    tgt_path: str,
    src_project_id: int = None,
    tgt_project_id: int = None
) -> int:
    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=src_project_id or _get_unique_project_id(),
            source_project_path=src_path,
            target_project_id=tgt_project_id or _get_unique_project_id(),
            target_project_path=tgt_path,
            enabled=True,
            last_update_status="pending",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        return m.id


@pytest.mark.asyncio
async def test_export_pair_mirrors_downloads_json(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="My Pair", src_id=src_id, tgt_id=tgt_id)
    await seed_mirror(session_maker, pair_id=pair_id, src_path="a/b", tgt_path="c/d")

    resp = await client.get(f"/api/export/pair/{pair_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment;" in resp.headers.get("content-disposition", "")

    data = json.loads(resp.text)
    # Check metadata structure
    assert "metadata" in data
    assert data["metadata"]["pair_name"] == "My Pair"
    assert data["metadata"]["total_mirrors"] == 1
    assert "exported_at" in data["metadata"]

    # Check mirrors array
    assert len(data["mirrors"]) == 1
    assert data["mirrors"][0]["source_project_path"] == "a/b"
    assert data["mirrors"][0]["target_project_path"] == "c/d"
    # Project IDs should NOT be in export
    assert "source_project_id" not in data["mirrors"][0]
    assert "target_project_id" not in data["mirrors"][0]


@pytest.mark.asyncio
async def test_import_pair_mirrors_imports_and_skips_duplicates(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    payload = {
        "mirrors": [
            {
                "source_project_path": "a/b",
                "target_project_path": "c/d",
                # Project IDs are looked up at import time, not in payload
                "mirror_overwrite_diverged": None,
                "mirror_trigger_builds": None,
                "only_mirror_protected_branches": None,
                "enabled": True,
            }
        ],
    }

    # Mock GitLab client to simulate actual mirror creation and project lookup
    with patch('app.api.mirrors.GitLabClient') as MockMirrorsClient, \
         patch('app.api.export.GitLabClient') as MockExportClient:

        # Mock for import endpoint (project lookup)
        mock_export_client = MagicMock()
        mock_export_client.get_project_by_path.side_effect = lambda path: {
            "id": 1 if path == "a/b" else 2,
            "path_with_namespace": path
        }
        MockExportClient.return_value = mock_export_client

        # Mock for mirrors creation
        mock_mirrors_client = MagicMock()
        mock_mirrors_client.create_project_access_token.return_value = {
            "id": 123,
            "token": "test-token-value"
        }
        mock_mirrors_client.create_push_mirror.return_value = {"id": 456}
        mock_mirrors_client.create_pull_mirror.return_value = {"id": 789}
        mock_mirrors_client.get_project_mirrors.return_value = []
        MockMirrorsClient.return_value = mock_mirrors_client

        resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert resp.json()["skipped"] == 0

        # Verify project lookup was called
        assert mock_export_client.get_project_by_path.called
        # Verify GitLab mirror creation was called
        assert mock_mirrors_client.create_project_access_token.called
        assert mock_mirrors_client.create_push_mirror.called or mock_mirrors_client.create_pull_mirror.called

    # Import again should skip existing
    with patch('app.api.export.GitLabClient') as MockExportClient2:
        mock_export_client2 = MagicMock()
        mock_export_client2.get_project_by_path.side_effect = lambda path: {
            "id": 1 if path == "a/b" else 2,
            "path_with_namespace": path
        }
        MockExportClient2.return_value = mock_export_client2

        resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] == 0
        assert result["skipped"] == 1
        # Verify skipped_details contains the mirror identifier
        assert "skipped_details" in result
        assert len(result["skipped_details"]) == 1
        assert "[1/1]" in result["skipped_details"][0]
        assert "a/b → c/d" in result["skipped_details"][0]


@pytest.mark.asyncio
async def test_export_pair_not_found(client):
    """Test exporting mirrors for non-existent pair returns 404."""
    resp = await client.get("/api/export/pair/9999")
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_export_pair_with_no_mirrors(client, session_maker):
    """Test exporting a pair with no mirrors returns empty list."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="empty-pair", src_id=src_id, tgt_id=tgt_id)

    resp = await client.get(f"/api/export/pair/{pair_id}")
    assert resp.status_code == 200
    data = json.loads(resp.text)
    assert data["metadata"]["pair_name"] == "empty-pair"
    assert data["metadata"]["total_mirrors"] == 0
    assert data["mirrors"] == []


@pytest.mark.asyncio
async def test_import_pair_not_found(client):
    """Test importing mirrors for non-existent pair returns 404."""
    payload = {
        "mirrors": [
            {
                "source_project_path": "a/b",
                "target_project_path": "c/d",
                "enabled": True,
            }
        ],
    }
    resp = await client.post("/api/export/pair/9999", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_import_pair_with_empty_mirrors_list(client, session_maker):
    """Test importing empty mirrors list succeeds with zero imported."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    payload = {"mirrors": []}

    resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 0
    assert resp.json()["skipped"] == 0
    assert resp.json()["errors"] == []


@pytest.mark.asyncio
async def test_import_pair_with_all_mirror_settings(client, session_maker):
    """Test importing mirrors with all settings populated."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    payload = {
        "mirrors": [
            {
                "source_project_path": "platform/core",
                "target_project_path": "mirror/platform-core",
                # Project IDs looked up at import time
                "mirror_overwrite_diverged": False,
                "mirror_trigger_builds": True,
                "only_mirror_protected_branches": True,
                "mirror_branch_regex": "^release/.*$",
                "enabled": True,
            }
        ],
    }

    # Mock GitLab clients
    with patch('app.api.mirrors.GitLabClient') as MockMirrorsClient, \
         patch('app.api.export.GitLabClient') as MockExportClient:

        # Mock project lookup
        mock_export_client = MagicMock()
        mock_export_client.get_project_by_path.side_effect = lambda path: {
            "id": 100 if path == "platform/core" else 200,
            "path_with_namespace": path
        }
        MockExportClient.return_value = mock_export_client

        # Mock mirror creation
        mock_mirrors_client = MagicMock()
        mock_mirrors_client.create_project_access_token.return_value = {
            "id": 123,
            "token": "test-token-value"
        }
        mock_mirrors_client.create_push_mirror.return_value = {"id": 456}
        mock_mirrors_client.create_pull_mirror.return_value = {"id": 789}
        mock_mirrors_client.get_project_mirrors.return_value = []
        MockMirrorsClient.return_value = mock_mirrors_client

        resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert resp.json()["errors"] == []

    # Verify the mirror was created with all settings
    from sqlalchemy import select
    async with session_maker() as s:
        result = await s.execute(
            select(Mirror).where(
                Mirror.instance_pair_id == pair_id,
                Mirror.source_project_path == "platform/core"
            )
        )
        mirror = result.scalar_one()
        # Direction comes from pair, not stored on mirror
        assert mirror.mirror_overwrite_diverged is False
        assert mirror.mirror_trigger_builds is True
        assert mirror.only_mirror_protected_branches is True
        assert mirror.mirror_branch_regex == "^release/.*$"
        assert mirror.enabled is True
        # Verify GitLab mirror was actually created
        assert mirror.mirror_id is not None
        assert mirror.gitlab_token_id is not None


@pytest.mark.asyncio
async def test_import_pair_multiple_mirrors(client, session_maker):
    """Test importing multiple mirrors at once."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    payload = {
        "mirrors": [
            {
                "source_project_path": "group/project1",
                "target_project_path": "mirror/project1",
                "enabled": True,
            },
            {
                "source_project_path": "group/project2",
                "target_project_path": "mirror/project2",
                "enabled": True,
            },
            {
                "source_project_path": "group/project3",
                "target_project_path": "mirror/project3",
                "enabled": False,
            },
        ],
    }

    # Mock GitLab clients
    with patch('app.api.mirrors.GitLabClient') as MockMirrorsClient, \
         patch('app.api.export.GitLabClient') as MockExportClient:

        # Mock project lookup with different IDs per project
        def get_project_id(path):
            project_ids = {
                "group/project1": 1, "mirror/project1": 10,
                "group/project2": 2, "mirror/project2": 20,
                "group/project3": 3, "mirror/project3": 30
            }
            return {"id": project_ids[path], "path_with_namespace": path}

        mock_export_client = MagicMock()
        mock_export_client.get_project_by_path.side_effect = get_project_id
        MockExportClient.return_value = mock_export_client

        # Mock mirror creation
        mock_mirrors_client = MagicMock()
        mock_mirrors_client.create_project_access_token.return_value = {
            "id": 123,
            "token": "test-token-value"
        }
        mock_mirrors_client.create_push_mirror.return_value = {"id": 456}
        mock_mirrors_client.create_pull_mirror.return_value = {"id": 789}
        mock_mirrors_client.get_project_mirrors.return_value = []
        MockMirrorsClient.return_value = mock_mirrors_client

        resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == []


@pytest.mark.asyncio
async def test_export_import_roundtrip(client, session_maker):
    """Test exporting mirrors and re-importing them to a different pair."""
    # Create first pair with mirrors
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair1_id = await seed_pair(session_maker, name="pair1", src_id=src_id, tgt_id=tgt_id)
    await seed_mirror(session_maker, pair_id=pair1_id, src_path="group/proj1", tgt_path="mirror/proj1")
    await seed_mirror(session_maker, pair_id=pair1_id, src_path="group/proj2", tgt_path="mirror/proj2")

    # Export from pair1
    export_resp = await client.get(f"/api/export/pair/{pair1_id}")
    assert export_resp.status_code == 200
    export_data = json.loads(export_resp.text)
    assert len(export_data["mirrors"]) == 2

    # Create second pair
    pair2_id = await seed_pair(session_maker, name="pair2", src_id=src_id, tgt_id=tgt_id)

    # Import to pair2 (mirrors only, metadata ignored)
    import_payload = {"mirrors": export_data["mirrors"]}

    # Mock GitLab clients for import
    with patch('app.api.mirrors.GitLabClient') as MockMirrorsClient, \
         patch('app.api.export.GitLabClient') as MockExportClient:

        # Mock project lookup
        def get_project_id(path):
            project_ids = {
                "group/proj1": 1, "mirror/proj1": 10,
                "group/proj2": 2, "mirror/proj2": 20
            }
            return {"id": project_ids.get(path, 999), "path_with_namespace": path}

        mock_export_client = MagicMock()
        mock_export_client.get_project_by_path.side_effect = get_project_id
        MockExportClient.return_value = mock_export_client

        # Mock mirror creation
        mock_mirrors_client = MagicMock()
        mock_mirrors_client.create_project_access_token.return_value = {
            "id": 123,
            "token": "test-token-value"
        }
        mock_mirrors_client.create_push_mirror.return_value = {"id": 456}
        mock_mirrors_client.create_pull_mirror.return_value = {"id": 789}
        mock_mirrors_client.get_project_mirrors.return_value = []
        MockMirrorsClient.return_value = mock_mirrors_client

        import_resp = await client.post(f"/api/export/pair/{pair2_id}", json=import_payload)
        assert import_resp.status_code == 200
        assert import_resp.json()["imported"] == 2
        assert import_resp.json()["skipped"] == 0

    # Verify pair2 now has the mirrors
    verify_resp = await client.get(f"/api/export/pair/{pair2_id}")
    verify_data = json.loads(verify_resp.text)
    assert len(verify_data["mirrors"]) == 2


@pytest.mark.asyncio
async def test_export_filename_sanitization(client, session_maker):
    """Test that export filenames are sanitized for special characters."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    # Create pair with special characters in name
    pair_id = await seed_pair(session_maker, name="My/Pair:Name*With?Chars", src_id=src_id, tgt_id=tgt_id)

    resp = await client.get(f"/api/export/pair/{pair_id}")
    assert resp.status_code == 200

    # Check that filename is sanitized
    content_disposition = resp.headers.get("content-disposition", "")
    assert "attachment;" in content_disposition
    # Should not contain special characters like /, :, *, ?
    assert "/" not in content_disposition.split("filename=")[1]
    assert ":" not in content_disposition.split("filename=")[1]
    assert "*" not in content_disposition.split("filename=")[1]
    assert "?" not in content_disposition.split("filename=")[1]


@pytest.mark.asyncio
async def test_import_mixed_success_and_skips(client, session_maker):
    """Test importing with some new mirrors and some duplicates."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    # Create one existing mirror with explicit IDs
    await seed_mirror(
        session_maker,
        pair_id=pair_id,
        src_path="group/existing",
        tgt_path="mirror/existing",
        src_project_id=100,
        tgt_project_id=200
    )

    # Try to import 3 mirrors: 1 existing (should skip), 2 new (should import)
    payload = {
        "mirrors": [
            {
                "source_project_path": "group/existing",
                "target_project_path": "mirror/existing",
                "enabled": True,
            },
            {
                "source_project_path": "group/new1",
                "target_project_path": "mirror/new1",
                "enabled": True,
            },
            {
                "source_project_path": "group/new2",
                "target_project_path": "mirror/new2",
                "enabled": True,
            },
        ],
    }

    # Mock GitLab clients
    with patch('app.api.mirrors.GitLabClient') as MockMirrorsClient, \
         patch('app.api.export.GitLabClient') as MockExportClient:

        # Mock project lookup
        def get_project_id(path):
            project_ids = {
                "group/existing": 100, "mirror/existing": 200,
                "group/new1": 10, "mirror/new1": 20,
                "group/new2": 30, "mirror/new2": 40
            }
            return {"id": project_ids[path], "path_with_namespace": path}

        mock_export_client = MagicMock()
        mock_export_client.get_project_by_path.side_effect = get_project_id
        MockExportClient.return_value = mock_export_client

        # Mock mirror creation
        mock_mirrors_client = MagicMock()
        mock_mirrors_client.create_project_access_token.return_value = {
            "id": 123,
            "token": "test-token-value"
        }
        mock_mirrors_client.create_push_mirror.return_value = {"id": 456}
        mock_mirrors_client.create_pull_mirror.return_value = {"id": 789}
        mock_mirrors_client.get_project_mirrors.return_value = []
        MockMirrorsClient.return_value = mock_mirrors_client

        resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] == 2
        assert result["skipped"] == 1
        assert result["errors"] == []
        # Verify skipped_details contains the mirror identifier
        assert "skipped_details" in result
        assert len(result["skipped_details"]) == 1
        assert "[1/3]" in result["skipped_details"][0]
        assert "group/existing → mirror/existing" in result["skipped_details"][0]
        assert "Already exists" in result["skipped_details"][0]

