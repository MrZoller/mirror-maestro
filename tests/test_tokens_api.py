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


@pytest.mark.asyncio
async def test_tokens_get_not_found(client):
    """Test 404 when getting non-existent token."""
    resp = await client.get("/api/tokens/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tokens_update_not_found(client):
    """Test 404 when updating non-existent token."""
    resp = await client.put("/api/tokens/9999", json={"token": "new-token"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tokens_delete_not_found(client):
    """Test 404 when deleting non-existent token."""
    resp = await client.delete("/api/tokens/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tokens_list_filtered_by_instance(client, session_maker):
    """Test listing tokens filtered by GitLab instance."""
    inst1_id = await seed_instance(session_maker, name="inst1")
    inst2_id = await seed_instance(session_maker, name="inst2")

    # Create tokens for both instances
    await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst1_id, "group_path": "group1", "token": "tok1", "token_name": "bot1"},
    )
    await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst1_id, "group_path": "group2", "token": "tok2", "token_name": "bot2"},
    )
    await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst2_id, "group_path": "group1", "token": "tok3", "token_name": "bot3"},
    )

    # Get all tokens
    resp = await client.get("/api/tokens")
    assert resp.status_code == 200
    all_tokens = resp.json()
    assert len(all_tokens) == 3

    # Filter by inst1
    resp = await client.get(f"/api/tokens?gitlab_instance_id={inst1_id}")
    assert resp.status_code == 200
    inst1_tokens = resp.json()
    assert len(inst1_tokens) == 2
    assert all(t["gitlab_instance_id"] == inst1_id for t in inst1_tokens)

    # Filter by inst2
    resp = await client.get(f"/api/tokens?gitlab_instance_id={inst2_id}")
    assert resp.status_code == 200
    inst2_tokens = resp.json()
    assert len(inst2_tokens) == 1
    assert inst2_tokens[0]["gitlab_instance_id"] == inst2_id


@pytest.mark.asyncio
async def test_tokens_update_group_path(client, session_maker):
    """Test updating only the group_path."""
    inst_id = await seed_instance(session_maker)

    # Create token
    resp = await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst_id, "group_path": "old/path", "token": "tok", "token_name": "bot"},
    )
    token_id = resp.json()["id"]

    # Update group_path
    resp = await client.put(f"/api/tokens/{token_id}", json={"group_path": "new/path"})
    assert resp.status_code == 200
    assert resp.json()["group_path"] == "new/path"


@pytest.mark.asyncio
async def test_tokens_update_token_name(client, session_maker):
    """Test updating only the token_name."""
    inst_id = await seed_instance(session_maker)

    # Create token
    resp = await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst_id, "group_path": "group", "token": "tok", "token_name": "old-bot"},
    )
    token_id = resp.json()["id"]

    # Update token_name
    resp = await client.put(f"/api/tokens/{token_id}", json={"token_name": "new-bot"})
    assert resp.status_code == 200
    assert resp.json()["token_name"] == "new-bot"


@pytest.mark.asyncio
async def test_tokens_update_multiple_fields(client, session_maker):
    """Test updating multiple fields at once."""
    inst_id = await seed_instance(session_maker)

    # Create token
    resp = await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst_id, "group_path": "group", "token": "old-tok", "token_name": "old-bot"},
    )
    token_id = resp.json()["id"]

    # Update multiple fields
    resp = await client.put(
        f"/api/tokens/{token_id}",
        json={"group_path": "new/group", "token": "new-tok", "token_name": "new-bot"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_path"] == "new/group"
    assert data["token_name"] == "new-bot"

    # Verify token was encrypted
    async with session_maker() as s:
        row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == token_id))).scalar_one()
        assert row.encrypted_token == "enc:new-tok"


@pytest.mark.asyncio
async def test_tokens_rotation_workflow(client, session_maker):
    """Test token rotation workflow (creating, updating, then deleting)."""
    inst_id = await seed_instance(session_maker)

    # Create initial token
    resp = await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst_id, "group_path": "group", "token": "token-v1", "token_name": "bot"},
    )
    token_id = resp.json()["id"]

    # Rotate token (update)
    resp = await client.put(f"/api/tokens/{token_id}", json={"token": "token-v2"})
    assert resp.status_code == 200

    # Verify new token is encrypted
    async with session_maker() as s:
        row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == token_id))).scalar_one()
        assert row.encrypted_token == "enc:token-v2"

    # Delete old token
    resp = await client.delete(f"/api/tokens/{token_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tokens_multiple_groups_same_instance(client, session_maker):
    """Test creating tokens for multiple groups on the same instance."""
    inst_id = await seed_instance(session_maker)

    # Create tokens for different groups
    groups = ["group1", "group2", "group1/subgroup"]
    for group in groups:
        resp = await client.post(
            "/api/tokens",
            json={
                "gitlab_instance_id": inst_id,
                "group_path": group,
                "token": f"token-{group}",
                "token_name": f"bot-{group}",
            },
        )
        assert resp.status_code == 200

    # List all tokens for this instance
    resp = await client.get(f"/api/tokens?gitlab_instance_id={inst_id}")
    assert resp.status_code == 200
    tokens = resp.json()
    assert len(tokens) == 3
    assert set(t["group_path"] for t in tokens) == set(groups)


@pytest.mark.asyncio
async def test_tokens_duplicate_prevention(client, session_maker):
    """Test that duplicate tokens for same group/instance are prevented."""
    inst_id = await seed_instance(session_maker)

    payload = {
        "gitlab_instance_id": inst_id,
        "group_path": "platform/core",
        "token": "tok",
        "token_name": "bot",
    }

    # Create first token - should succeed
    resp = await client.post("/api/tokens", json=payload)
    assert resp.status_code == 200

    # Try to create duplicate - should fail
    resp = await client.post("/api/tokens", json=payload)
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_tokens_update_empty_payload(client, session_maker):
    """Test updating token with empty payload doesn't change anything."""
    inst_id = await seed_instance(session_maker)

    # Create token
    resp = await client.post(
        "/api/tokens",
        json={"gitlab_instance_id": inst_id, "group_path": "group", "token": "tok", "token_name": "bot"},
    )
    token_id = resp.json()["id"]
    original_data = resp.json()

    # Update with empty payload
    resp = await client.put(f"/api/tokens/{token_id}", json={})
    assert resp.status_code == 200
    updated_data = resp.json()

    # Should be unchanged (except updated_at timestamp)
    assert updated_data["group_path"] == original_data["group_path"]
    assert updated_data["token_name"] == original_data["token_name"]
    assert updated_data["gitlab_instance_id"] == original_data["gitlab_instance_id"]


@pytest.mark.asyncio
async def test_tokens_list_empty(client):
    """Test listing tokens when none exist."""
    resp = await client.get("/api/tokens")
    assert resp.status_code == 200
    assert resp.json() == []

