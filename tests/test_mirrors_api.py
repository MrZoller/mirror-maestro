import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


class FakeGitLabClient:
    inits = []
    pull_calls = []
    push_calls = []
    trigger_calls = []
    trigger_pull_calls = []
    delete_calls = []
    delete_pull_calls = []
    update_calls = []
    update_pull_calls = []
    token_create_calls = []
    token_delete_calls = []
    project_mirrors = {}  # project_id -> list[dict] (push mirrors)
    pull_mirrors = {}  # project_id -> dict (pull mirror)

    def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
        self.url = url
        self.encrypted_token = encrypted_token
        self.__class__.inits.append((url, encrypted_token))

    def create_project_access_token(
        self,
        project_id: int,
        name: str,
        scopes: list,
        expires_at: str,
        access_level: int = 40,
    ):
        self.__class__.token_create_calls.append((project_id, name, scopes, expires_at, access_level))
        return {"id": 999, "name": name, "token": "fake-token-value", "scopes": scopes, "expires_at": expires_at}

    def delete_project_access_token(self, project_id: int, token_id: int) -> bool:
        self.__class__.token_delete_calls.append((project_id, token_id))
        return True

    def create_pull_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled=True,
        only_protected_branches=False,
        mirror_overwrites_diverged_branches=None,
        trigger_builds=None,
        mirror_branch_regex=None,
        auth_user=None,
        auth_password=None,
    ):
        self.__class__.pull_calls.append(
            (project_id, mirror_url, enabled, only_protected_branches, mirror_overwrites_diverged_branches, trigger_builds, mirror_branch_regex, auth_user, auth_password)
        )
        return {"id": 77}

    def create_push_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled=True,
        keep_divergent_refs=None,
        only_protected_branches=False,
        mirror_branch_regex=None,
    ):
        self.__class__.push_calls.append(
            (project_id, mirror_url, enabled, keep_divergent_refs, only_protected_branches, mirror_branch_regex)
        )
        return {"id": 88}

    def get_project_mirrors(self, project_id: int):
        """Get push mirrors (remote mirrors)."""
        return list(self.__class__.project_mirrors.get(project_id, []))

    def get_pull_mirror(self, project_id: int):
        """Get pull mirror configuration."""
        return self.__class__.pull_mirrors.get(project_id)

    def trigger_mirror_update(self, project_id: int, mirror_id: int) -> bool:
        """Trigger push mirror update."""
        self.__class__.trigger_calls.append((project_id, mirror_id))
        return True

    def trigger_pull_mirror_update(self, project_id: int) -> bool:
        """Trigger pull mirror update."""
        self.__class__.trigger_pull_calls.append((project_id,))
        return True

    def delete_mirror(self, project_id: int, mirror_id: int) -> bool:
        """Delete push mirror."""
        self.__class__.delete_calls.append((project_id, mirror_id))
        return True

    def delete_pull_mirror(self, project_id: int) -> bool:
        """Delete pull mirror."""
        self.__class__.delete_pull_calls.append((project_id,))
        return True

    def update_mirror(
        self,
        project_id: int,
        mirror_id: int,
        url=None,
        enabled=None,
        only_protected_branches=None,
        keep_divergent_refs=None,
        mirror_branch_regex=None,
    ):
        """Update push mirror settings."""
        self.__class__.update_calls.append(
            (
                project_id,
                mirror_id,
                url,
                enabled,
                only_protected_branches,
                keep_divergent_refs,
                mirror_branch_regex,
            )
        )
        # Reflect state change in fake data so subsequent reads see the update
        if enabled is not None and project_id in self.__class__.project_mirrors:
            for gm in self.__class__.project_mirrors[project_id]:
                if gm.get("id") == mirror_id:
                    gm["enabled"] = enabled
        return {"id": mirror_id}

    def update_pull_mirror(
        self,
        project_id: int,
        url=None,
        enabled=None,
        auth_user=None,
        auth_password=None,
        only_mirror_protected_branches=None,
        mirror_overwrites_diverged_branches=None,
        mirror_trigger_builds=None,
        mirror_branch_regex=None,
        import_url=None,
    ):
        """Update pull mirror settings."""
        self.__class__.update_pull_calls.append(
            (
                project_id,
                url,
                enabled,
                auth_user,
                auth_password,
                only_mirror_protected_branches,
                mirror_overwrites_diverged_branches,
                mirror_trigger_builds,
                mirror_branch_regex,
            )
        )
        # Reflect state change in fake data so subsequent reads see the update
        if enabled is not None and project_id in self.__class__.pull_mirrors:
            self.__class__.pull_mirrors[project_id]["enabled"] = enabled
        return {"id": project_id}


async def seed_instance(session_maker, *, name: str, url: str) -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url=url, encrypted_token="enc:t", description="", api_user_id=None, api_username=None)
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


async def seed_pair(session_maker, *, name: str, src_id: int, tgt_id: int, direction: str = "pull") -> int:
    async with session_maker() as s:
        pair = InstancePair(
            name=name,
            source_instance_id=src_id,
            target_instance_id=tgt_id,
            mirror_direction=direction,
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)
        return pair.id


@pytest.mark.asyncio
async def test_mirrors_trigger_update_updates_status(client, session_maker, monkeypatch):
    """Test triggering a pull mirror update uses the correct API."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_pull_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

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
        await s.refresh(m)
        mirror_id = m.id

    # Set up pull mirror data so refresh can find the mirror
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 77,
        "url": "https://src.example.com/platform/proj.git",
        "enabled": True,
        "update_status": "started",
        "last_update_at": "2024-01-15T10:30:00Z",
        "last_successful_update_at": None,
        "last_error": None,
    }

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200
    assert resp.json() == {"status": "update_triggered"}
    # Pull mirrors use trigger_pull_mirror_update which only takes project_id
    assert FakeGitLabClient.trigger_pull_calls[-1] == (2,)

    async with session_maker() as s:
        m2 = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        # After trigger, the endpoint refreshes status from GitLab.
        # GitLab 'started' maps to 'syncing' in our internal representation.
        assert m2.last_update_status == "syncing"
        # Timestamps should be populated from GitLab
        assert m2.last_update_at is not None


@pytest.mark.asyncio
async def test_mirrors_delete_best_effort_gitlab_and_db(client, session_maker, monkeypatch):
    """Test deleting a pull mirror uses the correct API."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_pull_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

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
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    # Pull mirrors use delete_pull_mirror which only takes project_id
    assert FakeGitLabClient.delete_pull_calls[-1] == (2,)

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_update_applies_settings_to_gitlab(client, session_maker, monkeypatch):
    """Test that updating a pull mirror uses the correct API with proper parameters."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_pull_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        # Pair defaults
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one()
        pair.mirror_overwrite_diverged = True
        pair.only_mirror_protected_branches = True
        pair.mirror_trigger_builds = True
        pair.mirror_branch_regex = "^main$"

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
        await s.refresh(m)
        db_mirror_id = m.id

    # Update enabled only; other values should be inherited from pair defaults.
    resp = await client.put(f"/api/mirrors/{db_mirror_id}", json={"enabled": False})
    assert resp.status_code == 200, resp.text

    # Pull direction => uses update_pull_mirror on target project_id (2)
    assert FakeGitLabClient.update_pull_calls[-1] == (
        2,       # project_id
        None,    # url (not updated)
        False,   # enabled
        None,    # auth_user (not updated)
        None,    # auth_password (not updated)
        True,    # only_mirror_protected_branches (pair default)
        True,    # mirror_overwrites_diverged_branches (pair default)
        True,    # mirror_trigger_builds (pair default)
        "^main$",  # mirror_branch_regex (pair default)
    )


@pytest.mark.asyncio
async def test_mirrors_create_pull_uses_auth_credentials(client, session_maker, monkeypatch):
    """Test that creating a pull mirror passes auth_user and auth_password correctly."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()
    FakeGitLabClient.token_create_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")

    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 201, resp.text

    # Pull mirrors should use auth_user (token name) and auth_password (token value)
    call = FakeGitLabClient.pull_calls[-1]
    # Signature: (project_id, mirror_url, enabled, only_protected_branches, mirror_overwrites_diverged_branches, trigger_builds, mirror_branch_regex, auth_user, auth_password)
    auth_user = call[7]
    auth_password = call[8]
    # auth_user should be the token name (starts with "mirror-maestro-")
    assert auth_user is not None
    assert auth_user.startswith("mirror-maestro-")
    # auth_password should be the fake token value
    assert auth_password == "fake-token-value"


@pytest.mark.asyncio
async def test_mirrors_update_can_clear_overrides_with_null(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_pull_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

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
            # Direction comes from pair, not stored on mirror
            mirror_overwrite_diverged=True,  # explicit override
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        db_mirror_id = m.id

    resp = await client.put(f"/api/mirrors/{db_mirror_id}", json={"mirror_overwrite_diverged": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mirror_overwrite_diverged"] is None

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == db_mirror_id))).scalar_one()
        assert row.mirror_overwrite_diverged is None


@pytest.mark.asyncio
async def test_mirrors_create_pull_conflicts_when_existing_pull_mirror_present(client, session_maker, monkeypatch):
    """Test that creating a pull mirror fails if one already exists on the project."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()
    FakeGitLabClient.token_create_calls.clear()
    FakeGitLabClient.token_delete_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Simulate an existing pull mirror on the target project using the pull_mirrors dict
    FakeGitLabClient.pull_mirrors[2] = {"id": 999, "url": "https://example.com/existing.git", "enabled": True}

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert "existing_pull_mirrors" in body["detail"]
    assert FakeGitLabClient.pull_calls == []


@pytest.mark.asyncio
async def test_mirrors_preflight_lists_existing_same_direction(client, session_maker, monkeypatch):
    """Test that preflight check for pull mirrors uses get_pull_mirror."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Simulate an existing pull mirror on the target project
    FakeGitLabClient.pull_mirrors[2] = {"id": 1, "url": "https://example.com/a.git", "enabled": True}

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["effective_direction"] == "pull"
    assert body["owner_project_id"] == 2
    assert len(body["existing_same_direction"]) == 1
    assert body["existing_same_direction"][0]["id"] == 1


@pytest.mark.asyncio
async def test_mirrors_remove_existing_deletes_same_direction(client, session_maker, monkeypatch):
    """Test that remove-existing for pull mirrors uses delete_pull_mirror."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Simulate an existing pull mirror on the target project
    FakeGitLabClient.pull_mirrors[2] = {"id": 11, "url": "https://example.com/a.git", "enabled": True}

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert body["deleted_ids"] == [11]
    # Pull mirrors use delete_pull_mirror which only takes project_id
    assert FakeGitLabClient.delete_pull_calls[-1] == (2,)


@pytest.mark.asyncio
async def test_mirrors_list_empty(client):
    """Test listing mirrors when none exist."""
    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['mirrors'] == []
    assert data['total'] == 0
    assert data['page'] == 1
    assert data['total_pages'] == 0


@pytest.mark.asyncio
async def test_mirrors_list_returns_all_mirrors(client, session_maker):
    """Test listing all mirrors."""
    # Create instances and pair
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)

        # Create multiple mirrors
        mirror1 = Mirror(
            instance_pair_id=pair.id,
            source_project_id=1,
            source_project_path="group/proj1",
            target_project_id=2,
            target_project_path="group/proj1",
            mirror_id=101,
            enabled=True,
            last_update_status="finished",
        )
        mirror2 = Mirror(
            instance_pair_id=pair.id,
            source_project_id=3,
            source_project_path="group/proj2",
            target_project_id=4,
            target_project_path="group/proj2",
            mirror_id=102,
            enabled=True,
            last_update_status="pending",
        )
        s.add_all([mirror1, mirror2])
        await s.commit()

    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    mirrors = data['mirrors']
    assert len(mirrors) == 2
    # Default order is created_at desc, so mirror2 (created last) appears first
    assert mirrors[0]["source_project_path"] == "group/proj2"
    assert mirrors[1]["source_project_path"] == "group/proj1"


@pytest.mark.asyncio
async def test_mirrors_get_by_id(client, session_maker):
    """Test getting a single mirror by ID."""
    # Create instance, pair, and mirror
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)

        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=123,
            source_project_path="group/project",
            target_project_id=456,
            target_project_path="group/project",
            mirror_id=789,
            enabled=True,
            last_update_status="finished",
        )
        s.add(mirror)
        await s.commit()
        await s.refresh(mirror)
        mirror_id = mirror.id

    resp = await client.get(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == mirror_id
    assert data["source_project_id"] == 123
    assert data["target_project_id"] == 456
    assert data["mirror_id"] == 789
    assert data["last_update_status"] == "finished"


@pytest.mark.asyncio
async def test_mirrors_get_not_found(client):
    """Test 404 when mirror doesn't exist."""
    resp = await client.get("/api/mirrors/9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_create_invalid_pair(client):
    """Test creating mirror with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "group/proj",
        "target_project_id": 2,
        "target_project_path": "group/proj",
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_create_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test error handling when GitLab API fails during mirror creation."""
    from app.api import mirrors as mod

    # Set up fake client that fails
    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
            pass

        def create_pull_mirror(self, *args, **kwargs):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    # Create instances and pair
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)
        pair_id = pair.id

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "group/proj",
        "target_project_id": 2,
        "target_project_path": "group/proj",
    }
    resp = await client.post("/api/mirrors", json=payload)
    # API returns 500 for unhandled exceptions during GitLab API calls
    assert resp.status_code in [400, 500]
    detail = resp.json()["detail"]
    assert "error" in detail.lower() or "failed" in detail.lower()


@pytest.mark.asyncio
async def test_mirrors_update_not_found(client):
    """Test updating a non-existent mirror."""
    payload = {"enabled": False}
    resp = await client.put("/api/mirrors/9999", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_delete_not_found(client):
    """Test deleting a non-existent mirror."""
    resp = await client.delete("/api/mirrors/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_trigger_update_not_found(client):
    """Test triggering update on non-existent mirror."""
    resp = await client.post("/api/mirrors/9999/update")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_update_multiple_settings(client, session_maker, monkeypatch):
    """Test updating multiple mirror settings at once."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update multiple settings
    payload = {
        "enabled": False,
        "mirror_overwrite_diverged": True,
        "only_mirror_protected_branches": True,
        "mirror_trigger_builds": True,
        "mirror_branch_regex": "^release/.*$",
    }
    resp = await client.put(f"/api/mirrors/{mirror_id}", json=payload)
    assert resp.status_code == 200

    data = resp.json()
    assert data["enabled"] is False
    assert data["mirror_overwrite_diverged"] is True
    assert data["only_mirror_protected_branches"] is True

    # Verify DB was updated
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert row.enabled is False
        assert row.mirror_overwrite_diverged is True


@pytest.mark.asyncio
async def test_mirrors_update_partial_settings(client, session_maker, monkeypatch):
    """Test updating only some settings leaves others unchanged."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            mirror_overwrite_diverged=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update only enabled status
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"enabled": False})
    assert resp.status_code == 200

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert row.enabled is False
        # Other settings unchanged
        assert row.mirror_overwrite_diverged is True


@pytest.mark.asyncio
async def test_mirrors_update_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test update continues even when GitLab API fails (best effort)."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
            pass

        def update_mirror(self, *args, **kwargs):
            raise Exception("GitLab API down")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update should fail with 500/400
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"enabled": False})
    assert resp.status_code in [400, 500]


@pytest.mark.asyncio
async def test_mirrors_delete_without_mirror_id(client, session_maker, monkeypatch):
    """Test deleting a mirror that was never created in GitLab."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=None,  # No GitLab mirror ID
            enabled=False,
            last_update_status="pending",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    # Should not have tried to delete from GitLab
    assert FakeGitLabClient.delete_calls == []

    # But should still delete from DB
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_delete_gitlab_api_failure_still_deletes_db(client, session_maker, monkeypatch):
    """Test delete still removes from DB even when GitLab API fails (best effort)."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
            pass

        def delete_pull_mirror(self, project_id: int):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Delete should succeed (best effort)
    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200

    # Should still be deleted from DB
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_trigger_update_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test trigger update error handling when GitLab API fails."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
            pass

        def trigger_pull_mirror_update(self, project_id: int):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code in [400, 500]


@pytest.mark.asyncio
async def test_mirrors_trigger_update_for_push_mirror(client, session_maker, monkeypatch):
    """Test triggering update for push mirror uses source project."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="push")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=10,
            source_project_path="platform/proj",
            target_project_id=20,
            target_project_path="platform/proj",
            mirror_id=88,
            # Direction comes from pair (push), not stored on mirror
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200

    # Push mirror should trigger on source project (direction from pair)
    assert FakeGitLabClient.trigger_calls[-1] == (10, 88)


@pytest.mark.asyncio
async def test_mirrors_list_filtered_by_pair(client, session_maker):
    """Test listing mirrors filtered by instance pair."""
    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair1_id = await seed_pair(session_maker, name="pair1", src_id=src_id, tgt_id=tgt_id, direction="pull")
    pair2_id = await seed_pair(session_maker, name="pair2", src_id=tgt_id, tgt_id=src_id, direction="push")

    async with session_maker() as s:
        # Create mirrors for pair1
        m1 = Mirror(
            instance_pair_id=pair1_id,
            source_project_id=1,
            source_project_path="group/proj1",
            target_project_id=2,
            target_project_path="group/proj1",
            mirror_id=101,
            enabled=True,
            last_update_status="finished",
        )
        m2 = Mirror(
            instance_pair_id=pair1_id,
            source_project_id=3,
            source_project_path="group/proj2",
            target_project_id=4,
            target_project_path="group/proj2",
            mirror_id=102,
            enabled=True,
            last_update_status="pending",
        )
        # Create mirror for pair2
        m3 = Mirror(
            instance_pair_id=pair2_id,
            source_project_id=5,
            source_project_path="group/proj3",
            target_project_id=6,
            target_project_path="group/proj3",
            mirror_id=103,
            enabled=True,
            last_update_status="failed",
        )
        s.add_all([m1, m2, m3])
        await s.commit()

    # Get all mirrors
    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 3
    assert len(data['mirrors']) == 3

    # Filter by pair1
    resp = await client.get(f"/api/mirrors?instance_pair_id={pair1_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    pair1_mirrors = data['mirrors']
    assert len(pair1_mirrors) == 2
    assert all(m["instance_pair_id"] == pair1_id for m in pair1_mirrors)

    # Filter by pair2
    resp = await client.get(f"/api/mirrors?instance_pair_id={pair2_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 1
    pair2_mirrors = data['mirrors']
    assert len(pair2_mirrors) == 1
    assert pair2_mirrors[0]["instance_pair_id"] == pair2_id


@pytest.mark.asyncio
async def test_mirrors_preflight_no_existing_mirrors(client, session_maker, monkeypatch):
    """Test preflight when no existing mirrors are present (pull direction)."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # No existing pull mirror (pull_mirrors[2] is not set or None)

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_direction"] == "pull"
    assert body["owner_project_id"] == 2
    assert body["existing_mirrors"] == []
    assert body["existing_same_direction"] == []


@pytest.mark.asyncio
async def test_mirrors_remove_existing_with_specific_ids(client, session_maker, monkeypatch):
    """Test removing specific push mirrors by ID (push mirrors allow multiple per project)."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    # Use push direction for this test since only push mirrors can have multiple per project
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="push")

    # Push mirrors are on source project (project 1)
    FakeGitLabClient.project_mirrors[1] = [
        {"id": 11, "url": "https://example.com/a.git"},
        {"id": 12, "url": "https://example.com/b.git"},
        {"id": 13, "url": "https://example.com/c.git"},
    ]

    # Remove only specific mirrors
    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "remote_mirror_ids": [11, 13],  # Only these two
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 2
    assert set(body["deleted_ids"]) == {11, 13}
    # Both should have been deleted
    assert (1, 11) in FakeGitLabClient.delete_calls
    assert (1, 13) in FakeGitLabClient.delete_calls
    assert (1, 12) not in FakeGitLabClient.delete_calls


@pytest.mark.asyncio
async def test_mirrors_preflight_invalid_pair(client):
    """Test preflight with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_remove_existing_invalid_pair(client):
    """Test remove-existing with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


# =============================================================================
# Mirror Verification Tests (Orphan/Drift Detection)
# =============================================================================


@pytest.mark.asyncio
async def test_verify_mirror_healthy(client, session_maker, monkeypatch):
    """Test verification returns healthy when mirror exists with matching settings."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-healthy", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-healthy", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-healthy", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=100,
            enabled=True,
            only_mirror_protected_branches=False,
            mirror_overwrite_diverged=False,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Mock GitLab to return a matching pull mirror (single dict, not a list)
    # Pull mirrors use different field names than push mirrors
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 100,
        "enabled": True,
        "only_mirror_protected_branches": False,
        "mirror_overwrites_diverged_branches": False,  # direct match for mirror_overwrite_diverged
        "mirror_trigger_builds": False,
        "mirror_branch_regex": None,
    }

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["orphan"] is False
    assert data["drift"] == []
    assert data["gitlab_mirror"] is not None


@pytest.mark.asyncio
async def test_verify_mirror_orphan(client, session_maker, monkeypatch):
    """Test verification detects orphan when mirror is deleted from GitLab."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-orphan", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-orphan", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-orphan", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=200,  # This ID won't exist on GitLab
            enabled=True,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab returns None for pull mirror (mirror was deleted/disabled)
    # Pull mirrors use get_pull_mirror which returns None if not configured
    FakeGitLabClient.pull_mirrors[2] = None

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "orphan"
    assert data["orphan"] is True
    assert data["gitlab_mirror"] is None


@pytest.mark.asyncio
async def test_verify_mirror_drift(client, session_maker, monkeypatch):
    """Test verification detects drift when settings mismatch."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-drift", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-drift", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-drift", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=300,
            enabled=True,  # We expect enabled=True
            only_mirror_protected_branches=True,  # We expect True
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab has different settings (pull mirror with different field names)
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 300,
        "enabled": False,  # Drifted from True
        "only_mirror_protected_branches": False,  # Drifted from True
        "mirror_overwrites_diverged_branches": False,
        "mirror_trigger_builds": False,
        "mirror_branch_regex": None,
    }

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "drift"
    assert data["orphan"] is False
    assert len(data["drift"]) >= 2

    # Check that enabled and only_protected_branches are in the drift list
    drift_fields = [d["field"] for d in data["drift"]]
    assert "enabled" in drift_fields
    assert "only_protected_branches" in drift_fields


@pytest.mark.asyncio
async def test_verify_mirror_not_created(client, session_maker, monkeypatch):
    """Test verification returns not_created when mirror has no mirror_id."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-nc", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-nc", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-nc", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=None,  # Not created on GitLab yet
            enabled=True,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_created"
    assert data["orphan"] is False


@pytest.mark.asyncio
async def test_verify_mirror_not_found(client):
    """Test verification returns 404 for non-existent mirror."""
    resp = await client.get("/api/mirrors/99999/verify")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_mirrors_batch(client, session_maker, monkeypatch):
    """Test batch verification of multiple mirrors."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-batch", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-batch", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-batch", src_id=src_id, tgt_id=tgt_id, direction="pull")

    mirror_ids = []
    async with session_maker() as s:
        # Create a healthy mirror
        m1 = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project1",
            target_project_id=10,
            target_project_path="group/project1-mirror",
            mirror_id=401,
            enabled=True,
        )
        s.add(m1)

        # Create an orphan mirror
        m2 = Mirror(
            instance_pair_id=pair_id,
            source_project_id=2,
            source_project_path="group/project2",
            target_project_id=20,
            target_project_path="group/project2-mirror",
            mirror_id=402,
            enabled=True,
        )
        s.add(m2)

        await s.commit()
        await s.refresh(m1)
        await s.refresh(m2)
        mirror_ids = [m1.id, m2.id]

    # Set up GitLab mock - only m1's mirror exists (using pull mirror format)
    FakeGitLabClient.pull_mirrors[10] = {
        "id": 401,
        "enabled": True,
        "only_mirror_protected_branches": False,
        "mirror_overwrites_diverged_branches": False,
        "mirror_trigger_builds": False,
        "mirror_branch_regex": None,
    }
    FakeGitLabClient.pull_mirrors[20] = None  # m2's mirror is gone (orphan)

    resp = await client.post("/api/mirrors/verify", json={"mirror_ids": mirror_ids})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2

    # Find each result by mirror_id
    result_by_id = {r["mirror_id"]: r for r in results}
    assert result_by_id[mirror_ids[0]]["status"] == "healthy"
    assert result_by_id[mirror_ids[1]]["status"] == "orphan"


@pytest.mark.asyncio
async def test_verify_mirrors_batch_empty(client):
    """Test batch verification with empty list returns empty result."""
    resp = await client.post("/api/mirrors/verify", json={"mirror_ids": []})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_issue_sync_enabled_two_tier_resolution(client, session_maker, monkeypatch):
    """Test that issue_sync_enabled follows the two-tier resolution pattern:
    mirror override → pair default."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()
    FakeGitLabClient.token_create_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")

    # Create pair with issue_sync_enabled=True
    resp = await client.post("/api/pairs", json={
        "name": "pair-issue-sync",
        "source_instance_id": src_id,
        "target_instance_id": tgt_id,
        "mirror_direction": "pull",
        "issue_sync_enabled": True,
    })
    assert resp.status_code == 201
    pair_id = resp.json()["id"]
    assert resp.json()["issue_sync_enabled"] is True

    # Create mirror without issue_sync_enabled override -> inherits from pair
    resp = await client.post("/api/mirrors", json={
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "group/proj",
        "target_project_id": 2,
        "target_project_path": "group/proj-mirror",
    })
    assert resp.status_code == 201
    data = resp.json()
    mirror_id = data["id"]
    assert data["issue_sync_enabled"] is None  # no override
    assert data["effective_issue_sync_enabled"] is True  # inherited from pair

    # Get mirror and verify
    resp = await client.get(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["issue_sync_enabled"] is None
    assert data["effective_issue_sync_enabled"] is True

    # Override at mirror level to False
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"issue_sync_enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["issue_sync_enabled"] is False
    assert data["effective_issue_sync_enabled"] is False  # mirror override wins

    # Clear the override (set to null) -> inherits from pair again
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"issue_sync_enabled": None})
    assert resp.status_code == 200
    data = resp.json()
    assert data["issue_sync_enabled"] is None
    assert data["effective_issue_sync_enabled"] is True  # back to pair default

    # Update pair to disable issue sync
    resp = await client.put(f"/api/pairs/{pair_id}", json={"issue_sync_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["issue_sync_enabled"] is False

    # Mirror should now inherit False
    resp = await client.get(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["issue_sync_enabled"] is None
    assert data["effective_issue_sync_enabled"] is False


@pytest.mark.asyncio
async def test_trigger_update_re_enables_paused_pull_mirror(client, session_maker, monkeypatch):
    """When a pull mirror is disabled/paused on GitLab, triggering sync should re-enable it first."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_pull_calls.clear()
    FakeGitLabClient.update_pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=False,
            last_update_status="failed",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab reports the mirror as disabled (paused due to failures)
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 77,
        "url": "https://src.example.com/platform/proj.git",
        "enabled": False,
        "update_status": "started",
        "last_update_at": "2024-01-15T10:30:00Z",
        "last_successful_update_at": None,
        "last_error": "13:fetch remote: fatal: remote error: ...",
    }

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "re_enabled_and_update_triggered"

    # update_pull_mirror should have been called with enabled=True
    assert len(FakeGitLabClient.update_pull_calls) == 1
    update_call = FakeGitLabClient.update_pull_calls[-1]
    # update_pull_calls format: (project_id, url, enabled, ...)
    assert update_call[0] == 2  # target project_id
    assert update_call[2] is True  # enabled=True

    # trigger should have been called
    assert len(FakeGitLabClient.trigger_pull_calls) == 1
    assert FakeGitLabClient.trigger_pull_calls[-1] == (2,)

    # Local DB should now have enabled=True
    async with session_maker() as s:
        m2 = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert m2.enabled is True


@pytest.mark.asyncio
async def test_trigger_update_re_enables_paused_push_mirror(client, session_maker, monkeypatch):
    """When a push mirror is disabled/paused on GitLab, triggering sync should re-enable it first."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_calls.clear()
    FakeGitLabClient.update_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="push")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=10,
            source_project_path="platform/proj",
            target_project_id=20,
            target_project_path="platform/proj",
            mirror_id=88,
            enabled=False,
            last_update_status="failed",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab reports the push mirror as disabled
    FakeGitLabClient.project_mirrors[10] = [
        {
            "id": 88,
            "url": "https://tgt.example.com/platform/proj.git",
            "enabled": False,
            "update_status": "started",
            "last_update_at": "2024-01-15T10:30:00Z",
            "last_successful_update_at": None,
            "last_error": "13:fetch remote: fatal: ...",
        }
    ]

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "re_enabled_and_update_triggered"

    # update_mirror should have been called with enabled=True
    assert len(FakeGitLabClient.update_calls) == 1
    update_call = FakeGitLabClient.update_calls[-1]
    # update_calls format: (project_id, mirror_id, url, enabled, ...)
    assert update_call[0] == 10  # source project_id
    assert update_call[1] == 88  # mirror_id
    assert update_call[3] is True  # enabled=True

    # trigger should have been called
    assert len(FakeGitLabClient.trigger_calls) == 1
    assert FakeGitLabClient.trigger_calls[-1] == (10, 88)


@pytest.mark.asyncio
async def test_trigger_update_skips_re_enable_for_enabled_mirror(client, session_maker, monkeypatch):
    """When a mirror is already enabled on GitLab, no re-enable call should be made."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_pull_calls.clear()
    FakeGitLabClient.update_pull_calls.clear()
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

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
        await s.refresh(m)
        mirror_id = m.id

    # Mirror is enabled on GitLab
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 77,
        "url": "https://src.example.com/platform/proj.git",
        "enabled": True,
        "update_status": "started",
        "last_update_at": "2024-01-15T10:30:00Z",
        "last_successful_update_at": None,
        "last_error": None,
    }

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "update_triggered"

    # update_pull_mirror should NOT have been called
    assert len(FakeGitLabClient.update_pull_calls) == 0

    # trigger should have been called
    assert len(FakeGitLabClient.trigger_pull_calls) == 1


@pytest.mark.asyncio
async def test_refresh_status_syncs_enabled_field(client, session_maker, monkeypatch):
    """Test that refreshing mirror status syncs the enabled field from GitLab."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="success",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab reports the mirror as disabled (auto-paused by GitLab)
    FakeGitLabClient.pull_mirrors[2] = {
        "id": 77,
        "url": "https://src.example.com/platform/proj.git",
        "enabled": False,
        "update_status": "failed",
        "last_update_at": "2024-01-15T10:30:00Z",
        "last_successful_update_at": "2024-01-14T08:00:00Z",
        "last_error": "13:fetch remote: fatal: ...",
    }

    resp = await client.post(f"/api/mirrors/{mirror_id}/refresh-status")
    assert resp.status_code == 200

    # Local DB should now reflect the disabled state from GitLab
    async with session_maker() as s:
        m2 = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert m2.enabled is False
        assert m2.last_update_status == "failed"
        assert m2.last_error == "13:fetch remote: fatal: ..."

