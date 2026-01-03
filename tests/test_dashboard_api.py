"""Tests for Dashboard API endpoints."""

import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient

from app.models import GitLabInstance, InstancePair, Mirror


class FakeGitLabClient:
    """Fake GitLab client for testing."""

    def __init__(self, url: str, encrypted_token: str):
        self.url = url

    def test_connection(self):
        return {"id": 1, "username": "test-user"}


@pytest.fixture
async def sample_data(db_session):
    """Create sample data for dashboard tests."""
    # Create instances
    instance1 = GitLabInstance(
        name="Instance 1",
        url="https://gitlab1.example.com",
        encrypted_token="enc:token1",
        api_user_id=1,
        api_username="user1"
    )
    instance2 = GitLabInstance(
        name="Instance 2",
        url="https://gitlab2.example.com",
        encrypted_token="enc:token2",
        api_user_id=2,
        api_username="user2"
    )
    db_session.add_all([instance1, instance2])
    await db_session.commit()
    await db_session.refresh(instance1)
    await db_session.refresh(instance2)

    # Create pairs
    pair1 = InstancePair(
        name="Pair 1",
        source_instance_id=instance1.id,
        target_instance_id=instance2.id,
        mirror_direction="push"
    )
    pair2 = InstancePair(
        name="Pair 2",
        source_instance_id=instance2.id,
        target_instance_id=instance1.id,
        mirror_direction="pull"
    )
    db_session.add_all([pair1, pair2])
    await db_session.commit()
    await db_session.refresh(pair1)
    await db_session.refresh(pair2)

    # Create mirrors with different statuses
    mirrors = [
        Mirror(
            instance_pair_id=pair1.id,
            source_project_id=1,
            source_project_path="group1/project1",
            target_project_id=101,
            target_project_path="group2/project1",
            enabled=True,
            last_update_status="success"
        ),
        Mirror(
            instance_pair_id=pair1.id,
            source_project_id=2,
            source_project_path="group1/project2",
            target_project_id=102,
            target_project_path="group2/project2",
            enabled=True,
            last_update_status="success"
        ),
        Mirror(
            instance_pair_id=pair1.id,
            source_project_id=3,
            source_project_path="group1/project3",
            target_project_id=103,
            target_project_path="group2/project3",
            enabled=True,
            last_update_status="failed"
        ),
        Mirror(
            instance_pair_id=pair2.id,
            source_project_id=4,
            source_project_path="group2/project4",
            target_project_id=104,
            target_project_path="group1/project4",
            enabled=True,
            last_update_status="pending"
        ),
        Mirror(
            instance_pair_id=pair2.id,
            source_project_id=5,
            source_project_path="group2/project5",
            target_project_id=105,
            target_project_path="group1/project5",
            enabled=False,
            last_update_status=None
        ),
    ]
    db_session.add_all(mirrors)
    await db_session.commit()

    return {
        "instances": [instance1, instance2],
        "pairs": [pair1, pair2],
        "mirrors": mirrors
    }


@pytest.mark.asyncio
async def test_get_dashboard_metrics(client: AsyncClient, sample_data):
    """Test getting dashboard metrics."""
    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200

    data = response.json()

    # Check summary
    assert "summary" in data
    summary = data["summary"]
    assert summary["total_mirrors"] == 5
    assert summary["total_pairs"] == 2
    assert summary["total_instances"] == 2
    assert summary["enabled_mirrors"] == 4  # 5 total - 1 disabled
    assert "health_percentage" in summary

    # Check health breakdown
    assert "health" in data
    health = data["health"]
    assert health["success"] == 2
    assert health["failed"] == 1
    assert health["pending"] == 1
    assert health["unknown"] == 1  # The disabled mirror with no status

    # Check recent activity exists
    assert "recent_activity" in data
    assert isinstance(data["recent_activity"], list)

    # Check mirrors by pair
    assert "mirrors_by_pair" in data
    assert isinstance(data["mirrors_by_pair"], list)


@pytest.mark.asyncio
async def test_get_dashboard_metrics_empty_database(client: AsyncClient):
    """Test dashboard metrics with empty database."""
    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200

    data = response.json()
    assert data["summary"]["total_mirrors"] == 0
    assert data["summary"]["total_pairs"] == 0
    assert data["summary"]["total_instances"] == 0
    assert data["summary"]["health_percentage"] == 100  # 100% when no mirrors


@pytest.mark.asyncio
async def test_get_dashboard_metrics_health_percentage(client: AsyncClient, sample_data):
    """Test health percentage calculation."""
    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200

    data = response.json()

    # 2 success out of 5 total = 40%
    assert data["summary"]["health_percentage"] == 40.0


@pytest.mark.asyncio
async def test_get_quick_stats(client: AsyncClient, sample_data):
    """Test getting quick stats."""
    response = await client.get("/api/dashboard/quick-stats")
    assert response.status_code == 200

    data = response.json()

    # Check expected fields
    assert "syncing_count" in data
    assert "recent_failures" in data
    assert "syncing_mirror_ids" in data
    assert "timestamp" in data

    # Verify types
    assert isinstance(data["syncing_count"], int)
    assert isinstance(data["recent_failures"], int)
    assert isinstance(data["syncing_mirror_ids"], list)


@pytest.mark.asyncio
async def test_get_quick_stats_empty_database(client: AsyncClient):
    """Test quick stats with empty database."""
    response = await client.get("/api/dashboard/quick-stats")
    assert response.status_code == 200

    data = response.json()
    assert data["syncing_count"] == 0
    assert data["recent_failures"] == 0
    assert data["syncing_mirror_ids"] == []


@pytest.mark.asyncio
async def test_recent_activity_ordering(client: AsyncClient, db_session):
    """Test that recent activity is ordered by updated_at descending."""
    # Create instance and pair
    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token="enc:token"
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)

    pair = InstancePair(
        name="Test Pair",
        source_instance_id=instance.id,
        target_instance_id=instance.id,
        mirror_direction="push"
    )
    db_session.add(pair)
    await db_session.commit()
    await db_session.refresh(pair)

    # Create mirrors with different updated_at times
    now = datetime.utcnow()
    mirrors = []
    for i in range(5):
        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=i + 1,
            source_project_path=f"group/project{i}",
            target_project_id=i + 101,
            target_project_path=f"group/mirror{i}",
            enabled=True,
            last_update_status="success"
        )
        db_session.add(mirror)
        await db_session.commit()
        await db_session.refresh(mirror)

        # Update timestamp manually to control ordering
        mirror.updated_at = now - timedelta(hours=i)
        await db_session.commit()
        mirrors.append(mirror)

    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200

    data = response.json()
    recent_activity = data["recent_activity"]

    # Verify ordering (most recent first)
    assert len(recent_activity) == 5
    for i in range(len(recent_activity) - 1):
        current = datetime.fromisoformat(recent_activity[i]["timestamp"])
        next_item = datetime.fromisoformat(recent_activity[i + 1]["timestamp"])
        assert current >= next_item


@pytest.mark.asyncio
async def test_mirrors_by_pair_counts(client: AsyncClient, sample_data):
    """Test mirrors by pair aggregation."""
    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200

    data = response.json()
    mirrors_by_pair = data["mirrors_by_pair"]

    # Should have data for both pairs
    assert len(mirrors_by_pair) <= 5  # Limited to top 5

    # Find the pair with 3 mirrors
    pair_counts = {p["pair_name"]: p["count"] for p in mirrors_by_pair}
    assert "Pair 1" in pair_counts
    assert pair_counts["Pair 1"] == 3

    assert "Pair 2" in pair_counts
    assert pair_counts["Pair 2"] == 2
