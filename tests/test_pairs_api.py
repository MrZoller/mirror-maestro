import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror, GroupMirrorDefaults


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
        "mirror_protected_branches": True,
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
        gd = GroupMirrorDefaults(
            instance_pair_id=pair_id,
            group_path="platform",
            mirror_direction="pull",
            mirror_overwrite_diverged=True,
        )
        s.add_all([m, gd])
        await s.commit()

    resp = await client.delete(f"/api/pairs/{pair_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    async with session_maker() as s:
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one_or_none()
        assert pair is None

        mirrors = (await s.execute(select(Mirror).where(Mirror.instance_pair_id == pair_id))).scalars().all()
        assert mirrors == []

        defaults = (await s.execute(select(GroupMirrorDefaults).where(GroupMirrorDefaults.instance_pair_id == pair_id))).scalars().all()
        assert defaults == []


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

