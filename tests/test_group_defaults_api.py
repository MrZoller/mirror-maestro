import pytest

from app.models import GitLabInstance, InstancePair


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


@pytest.mark.asyncio
async def test_group_defaults_upsert_list_delete(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    # Create
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "platform/core",
        "mirror_direction": "pull",
        "mirror_overwrite_diverged": True,
        "only_mirror_protected_branches": False,
        "mirror_trigger_builds": True,
        "mirror_branch_regex": "^main$",
        "mirror_user_id": 7,
    })
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["instance_pair_id"] == pair_id
    assert created["group_path"] == "platform/core"
    assert created["mirror_overwrite_diverged"] is True
    created_id = created["id"]

    # List
    resp = await client.get("/api/group-defaults")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["id"] == created_id for r in rows)

    # Upsert (same pair + group_path updates existing row)
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "platform/core",
        "mirror_direction": None,
        "mirror_overwrite_diverged": False,
        "only_mirror_protected_branches": True,
        "mirror_trigger_builds": None,
        "mirror_branch_regex": None,
        "mirror_user_id": None,
    })
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["id"] == created_id
    assert updated["mirror_overwrite_diverged"] is False
    assert updated["only_mirror_protected_branches"] is True

    # Delete
    resp = await client.delete(f"/api/group-defaults/{created_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_group_defaults_create_requires_valid_pair(client):
    """Test that creating group defaults requires a valid instance pair."""
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": 9999,
        "group_path": "platform/core",
        "mirror_direction": "pull",
    })
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_group_defaults_delete_not_found(client):
    """Test 404 when deleting non-existent group defaults."""
    resp = await client.delete("/api/group-defaults/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_group_defaults_list_filtered_by_pair(client, session_maker):
    """Test listing group defaults filtered by instance pair."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair1_id = await seed_pair(session_maker, name="pair1", src_id=src_id, tgt_id=tgt_id)
    pair2_id = await seed_pair(session_maker, name="pair2", src_id=tgt_id, tgt_id=src_id)

    # Create defaults for both pairs
    await client.post("/api/group-defaults", json={
        "instance_pair_id": pair1_id,
        "group_path": "group1",
        "mirror_direction": "pull",
    })
    await client.post("/api/group-defaults", json={
        "instance_pair_id": pair1_id,
        "group_path": "group2",
        "mirror_direction": "push",
    })
    await client.post("/api/group-defaults", json={
        "instance_pair_id": pair2_id,
        "group_path": "group1",
        "mirror_direction": "pull",
    })

    # Get all defaults
    resp = await client.get("/api/group-defaults")
    assert resp.status_code == 200
    all_defaults = resp.json()
    assert len(all_defaults) >= 3

    # Filter by pair1
    resp = await client.get(f"/api/group-defaults?instance_pair_id={pair1_id}")
    assert resp.status_code == 200
    pair1_defaults = resp.json()
    assert len(pair1_defaults) == 2
    assert all(d["instance_pair_id"] == pair1_id for d in pair1_defaults)

    # Filter by pair2
    resp = await client.get(f"/api/group-defaults?instance_pair_id={pair2_id}")
    assert resp.status_code == 200
    pair2_defaults = resp.json()
    assert len(pair2_defaults) == 1
    assert pair2_defaults[0]["instance_pair_id"] == pair2_id


@pytest.mark.asyncio
async def test_group_defaults_upsert_creates_new(client, session_maker):
    """Test that upsert creates new record when none exists."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    # First upsert creates new
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "new-group",
        "mirror_direction": "pull",
    })
    assert resp.status_code == 200
    first_id = resp.json()["id"]

    # Second upsert with same pair/group updates existing
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "new-group",
        "mirror_direction": "push",
    })
    assert resp.status_code == 200
    second_id = resp.json()["id"]
    assert second_id == first_id  # Same ID means update, not create
    assert resp.json()["mirror_direction"] == "push"


@pytest.mark.asyncio
async def test_group_defaults_upsert_different_groups(client, session_maker):
    """Test that upsert creates separate records for different groups."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    # Create for group1
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "group1",
        "mirror_direction": "pull",
    })
    assert resp.status_code == 200
    id1 = resp.json()["id"]

    # Create for group2 (different group)
    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "group2",
        "mirror_direction": "push",
    })
    assert resp.status_code == 200
    id2 = resp.json()["id"]

    # Should have different IDs
    assert id1 != id2


@pytest.mark.asyncio
async def test_group_defaults_with_all_settings(client, session_maker):
    """Test creating group defaults with all settings."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "platform/core",
        "mirror_direction": "pull",
        "mirror_protected_branches": True,
        "mirror_overwrite_diverged": False,
        "mirror_trigger_builds": True,
        "only_mirror_protected_branches": True,
        "mirror_branch_regex": "^release/.*$",
        "mirror_user_id": 42,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mirror_direction"] == "pull"
    assert data["mirror_protected_branches"] is True
    assert data["mirror_overwrite_diverged"] is False
    assert data["mirror_trigger_builds"] is True
    assert data["only_mirror_protected_branches"] is True
    assert data["mirror_branch_regex"] == "^release/.*$"
    assert data["mirror_user_id"] == 42


@pytest.mark.asyncio
async def test_group_defaults_with_null_settings(client, session_maker):
    """Test creating group defaults with null settings (inherit from pair)."""
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    resp = await client.post("/api/group-defaults", json={
        "instance_pair_id": pair_id,
        "group_path": "platform/core",
        "mirror_direction": None,
        "mirror_protected_branches": None,
        "mirror_overwrite_diverged": None,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mirror_direction"] is None
    assert data["mirror_protected_branches"] is None
    assert data["mirror_overwrite_diverged"] is None


@pytest.mark.asyncio
async def test_group_defaults_list_empty(client):
    """Test listing group defaults when none exist."""
    resp = await client.get("/api/group-defaults")
    assert resp.status_code == 200
    assert resp.json() == []

