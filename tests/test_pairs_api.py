import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair


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

