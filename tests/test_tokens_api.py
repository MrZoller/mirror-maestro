import pytest
from sqlalchemy import select

from app.models import GitLabInstance, GroupAccessToken


async def seed_instance(session_maker, *, name: str = "inst") -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url="https://x", encrypted_token="enc:t", description="")
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


@pytest.mark.asyncio
async def test_tokens_crud_and_uniqueness(client, session_maker):
    inst_id = await seed_instance(session_maker)

    resp = await client.get("/api/tokens")
    assert resp.status_code == 200
    assert resp.json() == []

    payload = {
        "gitlab_instance_id": inst_id,
        "group_path": "platform/core",
        "token": "tok",
        "token_name": "bot",
    }
    resp = await client.post("/api/tokens", json=payload)
    assert resp.status_code == 200
    token_id = resp.json()["id"]

    async with session_maker() as s:
        row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == token_id))).scalar_one()
        assert row.encrypted_token == "enc:tok"

    # Duplicate for same instance/group_path rejected
    resp = await client.post("/api/tokens", json=payload)
    assert resp.status_code == 400

    resp = await client.get(f"/api/tokens/{token_id}")
    assert resp.status_code == 200
    assert resp.json()["group_path"] == "platform/core"

    resp = await client.put(f"/api/tokens/{token_id}", json={"token": "tok2"})
    assert resp.status_code == 200

    async with session_maker() as s:
        row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == token_id))).scalar_one()
        assert row.encrypted_token == "enc:tok2"

    resp = await client.delete(f"/api/tokens/{token_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_tokens_create_requires_instance(client):
    resp = await client.post(
        "/api/tokens",
        json={
            "gitlab_instance_id": 999,
            "group_path": "x",
            "token": "t",
            "token_name": "n",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "GitLab instance not found"

