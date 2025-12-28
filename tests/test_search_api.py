"""Tests for the global search API endpoint."""

import pytest
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.encryption import encryption


@pytest.mark.asyncio
async def test_search_requires_query(client):
    """Test that search requires a query parameter."""
    response = await client.get("/api/search")
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_search_empty_database(client):
    """Test search on empty database returns no results."""
    response = await client.get("/api/search?q=test")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "test"
    assert data["total_count"] == 0
    assert data["instances"] == []
    assert data["pairs"] == []
    assert data["mirrors"] == []


@pytest.mark.asyncio
async def test_search_finds_instances(client, db_session, monkeypatch):
    """Test search finds matching instances."""
    # Create test instance
    instance = GitLabInstance(
        name="Production GitLab",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token"),
        description="Main production instance"
    )
    db_session.add(instance)
    await db_session.commit()

    # Search by name
    response = await client.get("/api/search?q=production")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["instances"]) == 1
    assert data["instances"][0]["title"] == "Production GitLab"
    assert data["instances"][0]["subtitle"] == "https://gitlab.example.com"


@pytest.mark.asyncio
async def test_search_finds_pairs(client, db_session, monkeypatch):
    """Test search finds matching pairs."""
    # Create instances first
    source = GitLabInstance(
        name="Source",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    target = GitLabInstance(
        name="Target",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add_all([source, target])
    await db_session.commit()

    # Create pair
    pair = InstancePair(
        name="Development to Production",
        source_instance_id=source.id,
        target_instance_id=target.id,
        mirror_direction="push",
        description="Syncs dev changes to prod"
    )
    db_session.add(pair)
    await db_session.commit()

    # Search by pair name
    response = await client.get("/api/search?q=development")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["pairs"]) == 1
    assert data["pairs"][0]["title"] == "Development to Production"
    assert data["pairs"][0]["subtitle"] == "push mirror"


@pytest.mark.asyncio
async def test_search_finds_mirrors(client, db_session, monkeypatch):
    """Test search finds matching mirrors."""
    # Create instances and pair
    source = GitLabInstance(
        name="Source",
        url="https://source.gitlab.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    target = GitLabInstance(
        name="Target",
        url="https://target.gitlab.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add_all([source, target])
    await db_session.commit()

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=source.id,
        target_instance_id=target.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=123,
        source_project_path="mygroup/awesome-project",
        target_project_id=456,
        target_project_path="mygroup/awesome-project-mirror",
        last_update_status="success"
    )
    db_session.add(mirror)
    await db_session.commit()

    # Search by project path
    response = await client.get("/api/search?q=awesome")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["mirrors"]) == 1
    assert "awesome-project" in data["mirrors"][0]["title"]
    assert data["mirrors"][0]["subtitle"] == "Status: success"


@pytest.mark.asyncio
async def test_search_across_all_types(client, db_session, monkeypatch):
    """Test search returns results from all entity types."""
    # Create instance with matching name
    instance = GitLabInstance(
        name="Acme GitLab",
        url="https://acme.gitlab.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    # Create pair with matching description
    pair = InstancePair(
        name="Main Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="pull",
        description="Acme internal mirroring"
    )
    db_session.add(pair)
    await db_session.commit()

    # Create mirror with matching project path
    mirror = Mirror(
        instance_pair_id=pair.id,
        source_project_id=1,
        source_project_path="acme/core-lib",
        target_project_id=2,
        target_project_path="backup/core-lib"
    )
    db_session.add(mirror)
    await db_session.commit()

    # Search for "acme"
    response = await client.get("/api/search?q=acme")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 3
    assert len(data["instances"]) == 1
    assert len(data["pairs"]) == 1
    assert len(data["mirrors"]) == 1


@pytest.mark.asyncio
async def test_search_limit_parameter(client, db_session, monkeypatch):
    """Test search respects limit parameter."""
    # Create multiple instances
    for i in range(10):
        instance = GitLabInstance(
            name=f"Test Instance {i}",
            url=f"https://test{i}.gitlab.com",
            encrypted_token=encryption.encrypt("test-token")
        )
        db_session.add(instance)
    await db_session.commit()

    # Search with limit
    response = await client.get("/api/search?q=test&limit=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data["instances"]) == 3


@pytest.mark.asyncio
async def test_search_case_insensitive(client, db_session, monkeypatch):
    """Test search is case-insensitive."""
    instance = GitLabInstance(
        name="MyGitLab",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()

    # Search with different cases
    for q in ["mygitlab", "MYGITLAB", "MyGitLab", "myGITLAB"]:
        response = await client.get(f"/api/search?q={q}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["instances"]) == 1, f"Failed for query: {q}"
