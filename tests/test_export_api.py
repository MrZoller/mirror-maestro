import json

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


async def seed_mirror(session_maker, *, pair_id: int, src_path: str, tgt_path: str) -> int:
    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path=src_path,
            target_project_id=2,
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
    assert data["pair_id"] == pair_id
    assert data["pair_name"] == "My Pair"
    assert len(data["mirrors"]) == 1
    assert data["mirrors"][0]["source_project_path"] == "a/b"


@pytest.mark.asyncio
async def test_import_pair_mirrors_imports_and_skips_duplicates(client, session_maker):
    src_id = await seed_instance(session_maker, name="src")
    tgt_id = await seed_instance(session_maker, name="tgt")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id)

    payload = {
        "pair_id": pair_id,
        "mirrors": [
            {
                "source_project_path": "a/b",
                "target_project_path": "c/d",
                "source_project_id": 1,
                "target_project_id": 2,
                "mirror_direction": None,
                "mirror_protected_branches": None,
                "mirror_overwrite_diverged": None,
                "mirror_trigger_builds": None,
                "only_mirror_protected_branches": None,
                "enabled": True,
            }
        ],
    }

    resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1
    assert resp.json()["skipped"] == 0

    # Import again should skip existing
    resp = await client.post(f"/api/export/pair/{pair_id}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 0
    assert resp.json()["skipped"] == 1

