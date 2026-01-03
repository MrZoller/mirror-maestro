import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


class FakeGitLabClient:
    """Mock GitLab client for tests."""
    def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
        self.url = url
        self.encrypted_token = encrypted_token

    def delete_mirror(self, project_id: int, mirror_id: int):
        """Mock delete_mirror for cleanup operations."""
        pass

    def delete_project_access_token(self, project_id: int, token_id: int):
        """Mock delete_project_access_token for cleanup operations."""
        pass


def patch_gitlab_client(monkeypatch, client_class):
    """Helper to patch GitLabClient in all modules that import it."""
    import app.core.gitlab_client
    import app.api.pairs
    import app.api.mirrors

    monkeypatch.setattr(app.core.gitlab_client, "GitLabClient", client_class)
    monkeypatch.setattr(app.api.pairs, "GitLabClient", client_class)
    monkeypatch.setattr(app.api.mirrors, "GitLabClient", client_class)


async def seed_instance(session_maker, *, name: str, url: str = "https://x") -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url=url, encrypted_token="enc:t", description="")
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


@pytest.mark.asyncio
async def test_pairs_create_list_update_delete(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    payload = {
        "name": "pair1",
        "source_instance_id": src_id,
        "target_instance_id": tgt_id,
        "mirror_direction": "pull",
        "mirror_overwrite_diverged": False,
        "mirror_trigger_builds": False,
        "only_mirror_protected_branches": False,
        "description": "d",
    }
    resp = await client.post("/api/pairs", json=payload)
    assert resp.status_code == 200
    pair_id = resp.json()["id"]

    resp = await client.get("/api/pairs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(f"/api/pairs/{pair_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "pair1"

    resp = await client.put(f"/api/pairs/{pair_id}", json={"mirror_direction": "push"})
    assert resp.status_code == 200
    assert resp.json()["mirror_direction"] == "push"

    resp = await client.delete(f"/api/pairs/{pair_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_pairs_delete_cascades_mirrors_and_group_defaults(client, session_maker, monkeypatch):
    """
    Test that deleting a pair also deletes associated mirrors from both
    the database and GitLab with proper rate limiting.
    """
    # Patch GitLabClient so cleanup operations use mock instead of real client
    patch_gitlab_client(monkeypatch, FakeGitLabClient)

    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "pair-cascade",
            "source_instance_id": src_id,
            "target_instance_id": tgt_id,
            "mirror_direction": "pull",
        },
    )
    assert resp.status_code == 200, resp.text
    pair_id = resp.json()["id"]

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="pending",
        )
        s.add(m)
        await s.commit()

    resp = await client.delete(f"/api/pairs/{pair_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    async with session_maker() as s:
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one_or_none()
        assert pair is None

        mirrors = (await s.execute(select(Mirror).where(Mirror.instance_pair_id == pair_id))).scalars().all()
        assert mirrors == []


@pytest.mark.asyncio
async def test_pairs_create_requires_instances(client, session_maker):
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "pair1",
            "source_instance_id": 999,
            "target_instance_id": tgt_id,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Source instance not found"


@pytest.mark.asyncio
async def test_pairs_cannot_change_instances_when_mirrors_exist(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    other_id = await seed_instance(session_maker, name="other")

    resp = await client.post(
        "/api/pairs",
        json={"name": "pair-lock", "source_instance_id": src_id, "target_instance_id": tgt_id, "mirror_direction": "pull"},
    )
    assert resp.status_code == 200, resp.text
    pair_id = resp.json()["id"]

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=None,
            enabled=True,
            last_update_status="pending",
        )
        s.add(m)
        await s.commit()

    resp = await client.put(f"/api/pairs/{pair_id}", json={"source_instance_id": other_id})
    assert resp.status_code == 400
    assert "cannot change" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pairs_get_not_found(client):
    """Test 404 when getting non-existent pair."""
    resp = await client.get("/api/pairs/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pairs_update_not_found(client):
    """Test 404 when updating non-existent pair."""
    resp = await client.put("/api/pairs/9999", json={"name": "test"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pairs_delete_not_found(client):
    """Test 404 when deleting non-existent pair."""
    resp = await client.delete("/api/pairs/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pairs_create_target_instance_not_found(client, session_maker):
    """Test creating pair with invalid target instance."""
    src_id = await seed_instance(session_maker, name="src")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "pair1",
            "source_instance_id": src_id,
            "target_instance_id": 999,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Target instance not found"


@pytest.mark.asyncio
async def test_pairs_create_with_all_settings(client, session_maker):
    """Test creating pair with all mirror settings."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "full-pair",
            "source_instance_id": src_id,
            "target_instance_id": tgt_id,
            "mirror_direction": "pull",
            "mirror_overwrite_diverged": False,
            "mirror_trigger_builds": True,
            "only_mirror_protected_branches": True,
            "mirror_branch_regex": "^main$",
            "description": "Test pair with all settings"
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mirror_direction"] == "pull"
    assert data["mirror_overwrite_diverged"] is False
    assert data["mirror_trigger_builds"] is True
    assert data["only_mirror_protected_branches"] is True
    assert data["mirror_branch_regex"] == "^main$"


@pytest.mark.asyncio
async def test_pairs_update_multiple_fields(client, session_maker):
    """Test updating multiple fields at once."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    # Create pair
    resp = await client.post(
        "/api/pairs",
        json={
            "name": "pair1",
            "source_instance_id": src_id,
            "target_instance_id": tgt_id,
            "mirror_direction": "push",
        },
    )
    pair_id = resp.json()["id"]

    # Update multiple fields
    resp = await client.put(
        f"/api/pairs/{pair_id}",
        json={
            "name": "renamed-pair",
            "description": "Updated description",
            "mirror_overwrite_diverged": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "renamed-pair"
    assert data["description"] == "Updated description"
    assert data["mirror_overwrite_diverged"] is True


@pytest.mark.asyncio
async def test_pairs_update_only_name(client, session_maker):
    """Test updating only the name field."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={"name": "pair1", "source_instance_id": src_id, "target_instance_id": tgt_id},
    )
    pair_id = resp.json()["id"]

    resp = await client.put(f"/api/pairs/{pair_id}", json={"name": "new-name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "new-name"


@pytest.mark.asyncio
async def test_pairs_update_mirror_settings(client, session_maker):
    """Test updating various mirror settings."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={"name": "pair1", "source_instance_id": src_id, "target_instance_id": tgt_id},
    )
    pair_id = resp.json()["id"]

    # Update mirror settings
    resp = await client.put(
        f"/api/pairs/{pair_id}",
        json={
            "mirror_overwrite_diverged": True,
            "mirror_trigger_builds": False,
            "mirror_branch_regex": "release/.*",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mirror_overwrite_diverged"] is True
    assert data["mirror_trigger_builds"] is False
    assert data["mirror_branch_regex"] == "release/.*"


@pytest.mark.asyncio
async def test_pairs_create_with_push_direction(client, session_maker):
    """Test creating pair with push direction."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "push-pair",
            "source_instance_id": src_id,
            "target_instance_id": tgt_id,
            "mirror_direction": "push",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["mirror_direction"] == "push"


@pytest.mark.asyncio
async def test_pairs_list_returns_all_pairs(client, session_maker):
    """Test listing multiple pairs."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")

    # Create multiple pairs
    await client.post(
        "/api/pairs",
        json={"name": "pair1", "source_instance_id": src_id, "target_instance_id": tgt_id},
    )
    await client.post(
        "/api/pairs",
        json={"name": "pair2", "source_instance_id": tgt_id, "target_instance_id": src_id},
    )

    resp = await client.get("/api/pairs")
    assert resp.status_code == 200
    pairs = resp.json()
    assert len(pairs) >= 2
    assert any(p["name"] == "pair1" for p in pairs)
    assert any(p["name"] == "pair2" for p in pairs)


@pytest.mark.asyncio
async def test_pairs_bidirectional_mirroring(client, session_maker):
    """
    Test bidirectional mirroring by creating pairs in both directions.

    This validates that:
    1. Two pairs can be created between the same instances (A→B and B→A)
    2. Each pair can have different settings
    3. Both pairs can be retrieved and have correct source/target
    4. Creating the reverse pair returns a warning about bidirectional mirroring
    """
    instance_a = await seed_instance(session_maker, name="Instance A", url="https://gitlab-a.example.com")
    instance_b = await seed_instance(session_maker, name="Instance B", url="https://gitlab-b.example.com")

    # Create A → B pair (push to backup) - no warning expected (first pair)
    resp_ab = await client.post(
        "/api/pairs",
        json={
            "name": "A to B (outbound)",
            "source_instance_id": instance_a,
            "target_instance_id": instance_b,
            "mirror_direction": "push",
            "mirror_overwrite_diverged": True,
            "description": "Push changes from A to B",
        },
    )
    assert resp_ab.status_code == 200
    pair_ab = resp_ab.json()
    assert pair_ab["source_instance_id"] == instance_a
    assert pair_ab["target_instance_id"] == instance_b
    assert pair_ab["mirror_direction"] == "push"
    assert pair_ab["mirror_overwrite_diverged"] is True
    assert pair_ab.get("warnings") is None  # No warning for first pair
    assert pair_ab.get("reverse_pair_id") is None

    # Create B → A pair (pull from backup) - warning expected (reverse pair exists)
    resp_ba = await client.post(
        "/api/pairs",
        json={
            "name": "B to A (inbound)",
            "source_instance_id": instance_b,
            "target_instance_id": instance_a,
            "mirror_direction": "pull",
            "mirror_overwrite_diverged": False,
            "description": "Pull changes from B to A",
        },
    )
    assert resp_ba.status_code == 200
    pair_ba = resp_ba.json()
    assert pair_ba["source_instance_id"] == instance_b
    assert pair_ba["target_instance_id"] == instance_a
    assert pair_ba["mirror_direction"] == "pull"
    assert pair_ba["mirror_overwrite_diverged"] is False
    # Should have bidirectional warning
    assert pair_ba.get("warnings") is not None
    assert len(pair_ba["warnings"]) == 1
    assert "Bidirectional mirroring detected" in pair_ba["warnings"][0]
    assert pair_ba["reverse_pair_id"] == pair_ab["id"]

    # Verify both pairs exist and are distinct
    resp = await client.get("/api/pairs")
    assert resp.status_code == 200
    pairs = resp.json()
    assert len(pairs) == 2

    # Verify we can get each pair individually
    resp_get_ab = await client.get(f"/api/pairs/{pair_ab['id']}")
    assert resp_get_ab.status_code == 200
    assert resp_get_ab.json()["name"] == "A to B (outbound)"

    resp_get_ba = await client.get(f"/api/pairs/{pair_ba['id']}")
    assert resp_get_ba.status_code == 200
    assert resp_get_ba.json()["name"] == "B to A (inbound)"


@pytest.mark.asyncio
async def test_pairs_bidirectional_with_mirrors(client, session_maker):
    """
    Test bidirectional mirroring with actual mirrors in each direction.

    This validates that mirrors can be created on pairs going both directions
    between the same two instances.
    """
    instance_a = await seed_instance(session_maker, name="Instance A")
    instance_b = await seed_instance(session_maker, name="Instance B")

    # Create bidirectional pairs
    resp_ab = await client.post(
        "/api/pairs",
        json={
            "name": "A to B",
            "source_instance_id": instance_a,
            "target_instance_id": instance_b,
            "mirror_direction": "push",
        },
    )
    assert resp_ab.status_code == 200
    pair_ab_id = resp_ab.json()["id"]

    resp_ba = await client.post(
        "/api/pairs",
        json={
            "name": "B to A",
            "source_instance_id": instance_b,
            "target_instance_id": instance_a,
            "mirror_direction": "push",
        },
    )
    assert resp_ba.status_code == 200
    pair_ba_id = resp_ba.json()["id"]

    # Add mirrors to both pairs
    async with session_maker() as s:
        # Mirror on A→B pair
        m1 = Mirror(
            instance_pair_id=pair_ab_id,
            source_project_id=100,
            source_project_path="group-a/project1",
            target_project_id=200,
            target_project_path="group-b/project1",
            enabled=True,
            last_update_status="finished",
        )
        # Mirror on B→A pair
        m2 = Mirror(
            instance_pair_id=pair_ba_id,
            source_project_id=201,
            source_project_path="group-b/project2",
            target_project_id=101,
            target_project_path="group-a/project2",
            enabled=True,
            last_update_status="finished",
        )
        s.add_all([m1, m2])
        await s.commit()

    # Verify both pairs still exist and have their mirrors
    resp = await client.get("/api/pairs")
    pairs = resp.json()
    pair_names = {p["name"] for p in pairs}
    assert "A to B" in pair_names
    assert "B to A" in pair_names

    # Deleting one pair should not affect the other
    resp_del = await client.delete(f"/api/pairs/{pair_ab_id}")
    assert resp_del.status_code == 200

    resp = await client.get("/api/pairs")
    pairs = resp.json()
    assert len(pairs) == 1
    assert pairs[0]["name"] == "B to A"


@pytest.mark.asyncio
async def test_pairs_create_self_referential_rejected(client, session_maker):
    """Test that creating a self-referential pair (A→A) is rejected."""
    instance_a = await seed_instance(session_maker, name="Instance A")

    resp = await client.post(
        "/api/pairs",
        json={
            "name": "Self Mirror",
            "source_instance_id": instance_a,
            "target_instance_id": instance_a,  # Same as source
            "mirror_direction": "push",
        },
    )
    assert resp.status_code == 422  # Validation error from Pydantic
    error_detail = resp.json()["detail"]
    # Pydantic returns validation errors in a specific format
    assert any("cannot mirror an instance to itself" in str(e).lower() for e in error_detail)


@pytest.mark.asyncio
async def test_pairs_update_to_self_referential_rejected(client, session_maker):
    """Test that updating a pair to be self-referential is rejected."""
    instance_a = await seed_instance(session_maker, name="Instance A")
    instance_b = await seed_instance(session_maker, name="Instance B")

    # Create valid pair first
    resp = await client.post(
        "/api/pairs",
        json={
            "name": "Valid Pair",
            "source_instance_id": instance_a,
            "target_instance_id": instance_b,
        },
    )
    assert resp.status_code == 200
    pair_id = resp.json()["id"]

    # Try to update target to match source
    resp = await client.put(
        f"/api/pairs/{pair_id}",
        json={"target_instance_id": instance_a},  # Would make it A→A
    )
    assert resp.status_code == 400
    assert "cannot mirror an instance to itself" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pairs_update_creates_bidirectional_warning(client, session_maker):
    """Test that updating a pair to create bidirectional mirroring returns a warning."""
    instance_a = await seed_instance(session_maker, name="Instance A")
    instance_b = await seed_instance(session_maker, name="Instance B")
    instance_c = await seed_instance(session_maker, name="Instance C")

    # Create A → B pair
    resp_ab = await client.post(
        "/api/pairs",
        json={
            "name": "A to B",
            "source_instance_id": instance_a,
            "target_instance_id": instance_b,
        },
    )
    assert resp_ab.status_code == 200
    pair_ab_id = resp_ab.json()["id"]

    # Create C → A pair (unrelated)
    resp_ca = await client.post(
        "/api/pairs",
        json={
            "name": "C to A",
            "source_instance_id": instance_c,
            "target_instance_id": instance_a,
        },
    )
    assert resp_ca.status_code == 200
    pair_ca_id = resp_ca.json()["id"]
    assert resp_ca.json().get("warnings") is None  # No warning (not a reverse)

    # Update C→A to become B→A (reverse of A→B)
    resp_update = await client.put(
        f"/api/pairs/{pair_ca_id}",
        json={"source_instance_id": instance_b, "name": "B to A"},
    )
    assert resp_update.status_code == 200
    data = resp_update.json()
    # Should have bidirectional warning
    assert data.get("warnings") is not None
    assert len(data["warnings"]) == 1
    assert "Bidirectional mirroring detected" in data["warnings"][0]
    assert data["reverse_pair_id"] == pair_ab_id

