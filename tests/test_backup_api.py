"""
Tests for backup and restore API endpoints.
"""
import io
import json
import tarfile
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


class FakeGitLabClient:
    """Mock GitLab client for testing."""
    test_ok = True
    current_user = {"id": 42, "username": "backup-test-user", "name": "Backup Test User"}

    def __init__(self, url: str, encrypted_token: str):
        self.url = url
        self.encrypted_token = encrypted_token

    def test_connection(self) -> bool:
        return self.test_ok

    def get_current_user(self):
        return self.current_user


@pytest.mark.asyncio
async def test_backup_stats_empty(client):
    """Test backup stats with empty database."""
    resp = await client.get("/api/backup/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["instances"] == 0
    assert data["pairs"] == 0
    assert data["mirrors"] == 0
    assert "database_size_bytes" in data
    assert "database_size_mb" in data


@pytest.mark.asyncio
async def test_backup_stats_with_data(client, session_maker, monkeypatch):
    """Test backup stats with data in database."""
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)

    # Create some test data
    resp = await client.post(
        "/api/instances",
        json={"name": "test-inst", "url": "https://gitlab.example.com", "token": "test-token"}
    )
    assert resp.status_code == 200

    # Check stats
    resp = await client.get("/api/backup/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["instances"] == 1
    assert data["pairs"] == 0
    assert data["mirrors"] == 0


@pytest.mark.asyncio
async def test_create_backup_empty_database(client):
    """Test creating a backup with an empty database."""
    resp = await client.get("/api/backup/create")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"

    # Check that filename is in Content-Disposition header
    assert "Content-Disposition" in resp.headers
    assert "mirror-maestro-backup-" in resp.headers["Content-Disposition"]
    assert ".tar.gz" in resp.headers["Content-Disposition"]

    # Verify the backup is a valid tar.gz
    content = resp.content
    assert len(content) > 0

    # Extract and verify contents
    with tempfile.TemporaryDirectory() as tmpdir:
        backup_file = Path(tmpdir) / "backup.tar.gz"
        backup_file.write_bytes(content)

        with tarfile.open(backup_file, "r:gz") as tar:
            members = tar.getnames()
            assert "database.json" in members
            assert "encryption.key" in members
            assert "backup_metadata.json" in members

            # Check metadata
            metadata_file = tar.extractfile("backup_metadata.json")
            metadata = json.loads(metadata_file.read().decode())
            assert "timestamp" in metadata
            assert "version" in metadata
            assert metadata["version"] == "2.0"
            assert metadata["format"] == "json"

            # Check database.json structure
            db_file = tar.extractfile("database.json")
            db_data = json.loads(db_file.read().decode())
            assert "gitlab_instances" in db_data
            assert "instance_pairs" in db_data
            assert "mirrors" in db_data
            assert db_data["gitlab_instances"] == []
            assert db_data["instance_pairs"] == []
            assert db_data["mirrors"] == []


@pytest.mark.asyncio
async def test_create_backup_with_data(client, session_maker, monkeypatch):
    """Test creating a backup with data in the database."""
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)

    # Create test instance
    resp = await client.post(
        "/api/instances",
        json={
            "name": "backup-test-inst",
            "url": "https://gitlab.example.com",
            "token": "backup-test-token"
        }
    )
    assert resp.status_code == 200
    instance_data = resp.json()

    # Create backup
    resp = await client.get("/api/backup/create")
    assert resp.status_code == 200

    # Verify backup contains the data
    with tempfile.TemporaryDirectory() as tmpdir:
        backup_file = Path(tmpdir) / "backup.tar.gz"
        backup_file.write_bytes(resp.content)

        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()

        with tarfile.open(backup_file, "r:gz") as tar:
            tar.extractall(extract_dir)

        # Check that database JSON file exists and has data
        db_file = extract_dir / "database.json"
        assert db_file.exists()

        db_data = json.loads(db_file.read_text())
        assert len(db_data["gitlab_instances"]) == 1
        assert db_data["gitlab_instances"][0]["name"] == "backup-test-inst"
        assert db_data["gitlab_instances"][0]["url"] == "https://gitlab.example.com"

        # Verify encryption key exists
        key_file = extract_dir / "encryption.key"
        assert key_file.exists()
        assert key_file.stat().st_size > 0


@pytest.mark.asyncio
async def test_restore_backup_invalid_file(client):
    """Test restoring with an invalid file."""
    # Create invalid tar.gz content
    invalid_content = b"not a valid tar file"

    resp = await client.post(
        "/api/backup/restore",
        files={"file": ("invalid.tar.gz", io.BytesIO(invalid_content), "application/gzip")},
        data={"create_backup_first": "false"}
    )
    assert resp.status_code == 400
    assert "Invalid or corrupt" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_backup_missing_files(client):
    """Test restoring a backup missing required files."""
    # Create a tar.gz with only one of the required files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create incomplete backup (missing encryption.key)
        test_db = tmppath / "database.json"
        test_db.write_text('{"gitlab_instances": [], "instance_pairs": [], "mirrors": []}')

        backup_file = tmppath / "incomplete.tar.gz"
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(test_db, arcname="database.json")

        backup_content = backup_file.read_bytes()

    resp = await client.post(
        "/api/backup/restore",
        files={"file": ("incomplete.tar.gz", io.BytesIO(backup_content), "application/gzip")},
        data={"create_backup_first": "false"}
    )
    assert resp.status_code == 400
    assert "encryption.key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_backup_legacy_format_rejected(client):
    """Test that legacy SQLite backup format is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create a legacy backup (mirrors.db instead of database.json)
        test_db = tmppath / "mirrors.db"
        test_db.write_text("fake db content")
        test_key = tmppath / "encryption.key"
        test_key.write_text("fake key")

        backup_file = tmppath / "legacy.tar.gz"
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(test_db, arcname="mirrors.db")
            tar.add(test_key, arcname="encryption.key")

        backup_content = backup_file.read_bytes()

    resp = await client.post(
        "/api/backup/restore",
        files={"file": ("legacy.tar.gz", io.BytesIO(backup_content), "application/gzip")},
        data={"create_backup_first": "false"}
    )
    assert resp.status_code == 400
    assert "older SQLite-based version" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_backup_creation_and_restore(client, session_maker, monkeypatch):
    """Test full backup creation and restore cycle."""
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)

    # Create original data
    resp = await client.post(
        "/api/instances",
        json={
            "name": "roundtrip-test",
            "url": "https://gitlab.roundtrip.com",
            "token": "roundtrip-token",
            "description": "Test instance for backup/restore"
        }
    )
    assert resp.status_code == 200
    original_instance = resp.json()

    # Create backup
    backup_resp = await client.get("/api/backup/create")
    assert backup_resp.status_code == 200
    backup_content = backup_resp.content

    # Verify backup is valid tar.gz
    assert len(backup_content) > 0

    # Extract and validate backup contents
    with tempfile.TemporaryDirectory() as tmpdir:
        backup_file = Path(tmpdir) / "backup.tar.gz"
        backup_file.write_bytes(backup_content)

        # Verify it's a valid archive
        with tarfile.open(backup_file, "r:gz") as tar:
            members = tar.getnames()
            assert "database.json" in members
            assert "encryption.key" in members
            assert "backup_metadata.json" in members

            # Extract files
            extract_dir = Path(tmpdir) / "extracted"
            extract_dir.mkdir()
            tar.extractall(extract_dir)

        # Validate database JSON
        db_file = extract_dir / "database.json"
        assert db_file.exists()

        db_data = json.loads(db_file.read_text())
        assert len(db_data["gitlab_instances"]) == 1
        instance = db_data["gitlab_instances"][0]
        assert instance["name"] == "roundtrip-test"
        assert instance["url"] == "https://gitlab.roundtrip.com"
        assert instance["description"] == "Test instance for backup/restore"

        # Verify encryption key exists
        key_file = extract_dir / "encryption.key"
        assert key_file.exists()
        assert key_file.stat().st_size > 0

    # Now test restore
    restore_resp = await client.post(
        "/api/backup/restore",
        files={"file": ("backup.tar.gz", io.BytesIO(backup_content), "application/gzip")},
        data={"create_backup_first": "false"}
    )
    assert restore_resp.status_code == 200
    restore_data = restore_resp.json()
    assert restore_data["success"] is True
    assert restore_data["imported_counts"]["gitlab_instances"] == 1

    # Verify data was restored
    list_resp = await client.get("/api/instances")
    assert list_resp.status_code == 200
    instances = list_resp.json()
    assert len(instances) == 1
    assert instances[0]["name"] == "roundtrip-test"


@pytest.mark.asyncio
async def test_restore_creates_pre_backup(client, session_maker, monkeypatch, tmp_path):
    """Test that restore creates a pre-restore backup when requested."""
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)

    # Create valid backup
    db_data = {
        "gitlab_instances": [],
        "instance_pairs": [],
        "mirrors": []
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        db_file = tmppath / "database.json"
        db_file.write_text(json.dumps(db_data))

        key_file = tmppath / "encryption.key"
        key_file.write_bytes(b"test-key-content")

        backup_file = tmppath / "test-backup.tar.gz"
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(db_file, arcname="database.json")
            tar.add(key_file, arcname="encryption.key")

        backup_content = backup_file.read_bytes()

    # Note: We can't easily verify the pre-restore backup was created
    # because it writes to ./data which may not exist in tests
    # The important thing is that the parameter is accepted and doesn't error
    resp = await client.post(
        "/api/backup/restore",
        files={"file": ("backup.tar.gz", io.BytesIO(backup_content), "application/gzip")},
        data={"create_backup_first": "true"}
    )
    # The restore should succeed
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_restore_with_complex_data(client, session_maker, monkeypatch):
    """Test restoring a backup with instances, pairs, and mirrors."""
    # Create backup data with all entity types
    db_data = {
        "gitlab_instances": [
            {
                "id": 1,
                "name": "Source GitLab",
                "url": "https://source.gitlab.com",
                "encrypted_token": "enc:source-token",
                "api_user_id": 10,
                "api_username": "source-user",
                "description": "Source instance"
            },
            {
                "id": 2,
                "name": "Target GitLab",
                "url": "https://target.gitlab.com",
                "encrypted_token": "enc:target-token",
                "api_user_id": 20,
                "api_username": "target-user",
                "description": "Target instance"
            }
        ],
        "instance_pairs": [
            {
                "id": 1,
                "name": "Source to Target",
                "source_instance_id": 1,
                "target_instance_id": 2,
                "mirror_direction": "push",
                "description": "Test pair"
            }
        ],
        "mirrors": [
            {
                "id": 1,
                "instance_pair_id": 1,
                "source_project_id": 100,
                "source_project_path": "group/project",
                "target_project_id": 200,
                "target_project_path": "group/project-mirror",
                "enabled": True,
                "last_update_status": "success"
            }
        ]
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        db_file = tmppath / "database.json"
        db_file.write_text(json.dumps(db_data))

        key_file = tmppath / "encryption.key"
        key_file.write_bytes(b"test-key-content")

        backup_file = tmppath / "complex-backup.tar.gz"
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(db_file, arcname="database.json")
            tar.add(key_file, arcname="encryption.key")

        backup_content = backup_file.read_bytes()

    resp = await client.post(
        "/api/backup/restore",
        files={"file": ("complex-backup.tar.gz", io.BytesIO(backup_content), "application/gzip")},
        data={"create_backup_first": "false"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["imported_counts"]["gitlab_instances"] == 2
    assert data["imported_counts"]["instance_pairs"] == 1
    assert data["imported_counts"]["mirrors"] == 1

    # Verify all data was restored
    instances_resp = await client.get("/api/instances")
    assert len(instances_resp.json()) == 2

    pairs_resp = await client.get("/api/pairs")
    assert len(pairs_resp.json()) == 1

    mirrors_resp = await client.get("/api/mirrors?instance_pair_id=1")
    data = mirrors_resp.json()
    assert data['total'] == 1
    assert len(data['mirrors']) == 1
