import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


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
async def test_pairs_delete_cascades_mirrors_and_group_defaults(client, session_maker):
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

