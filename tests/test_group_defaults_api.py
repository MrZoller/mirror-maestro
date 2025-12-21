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

