import pytest


def test_gitlab_client_test_connection_true(monkeypatch):
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.url = url
            self.private_token = private_token

        def auth(self):
            return True

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    assert client.test_connection() is True


def test_gitlab_client_test_connection_false(monkeypatch):
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def auth(self):
            raise RuntimeError("nope")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    assert client.test_connection() is False


def test_gitlab_client_get_projects_shapes(monkeypatch):
    from app.core import gitlab_client as mod

    class P:
        def __init__(self, id, name, path, pwn):
            self.id = id
            self.name = name
            self.path = path
            self.path_with_namespace = pwn
            self.description = "d"
            self.http_url_to_repo = "http"
            self.ssh_url_to_repo = "ssh"

    class Projects:
        def list(self, **kwargs):
            assert kwargs["get_all"] is False
            assert kwargs["page"] == 1
            return [P(1, "n", "p", "g/p")]

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.projects = Projects()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    projects = client.get_projects(search="x")
    assert projects == [
        {
            "id": 1,
            "name": "n",
            "path": "p",
            "path_with_namespace": "g/p",
            "description": "d",
            "http_url_to_repo": "http",
            "ssh_url_to_repo": "ssh",
        }
    ]


def test_gitlab_client_get_current_user_shapes(monkeypatch):
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.url = url
            self.private_token = private_token

        def http_get(self, path):
            assert path == "/user"
            return {"id": 7, "username": "bot", "name": "Bot"}

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    assert client.get_current_user() == {"id": 7, "username": "bot", "name": "Bot"}


def test_gitlab_client_get_project(monkeypatch):
    """Test fetching a single project by ID."""
    from app.core import gitlab_client as mod

    class P:
        def __init__(self):
            self.id = 42
            self.name = "my-project"
            self.path = "my-project"
            self.path_with_namespace = "group/my-project"
            self.description = "Test project"
            self.http_url_to_repo = "https://gitlab.com/group/my-project.git"
            self.ssh_url_to_repo = "git@gitlab.com:group/my-project.git"

    class Projects:
        def get(self, project_id):
            assert project_id == 42
            return P()

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.projects = Projects()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    project = client.get_project(42)
    assert project["id"] == 42
    assert project["name"] == "my-project"
    assert project["path_with_namespace"] == "group/my-project"


def test_gitlab_client_get_project_error(monkeypatch):
    """Test error handling when fetching a project fails."""
    from app.core import gitlab_client as mod

    class Projects:
        def get(self, project_id):
            raise Exception("Project not found")

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.projects = Projects()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to fetch project"):
        client.get_project(999)


def test_gitlab_client_get_groups(monkeypatch):
    """Test fetching groups with pagination."""
    from app.core import gitlab_client as mod

    class G:
        def __init__(self, id, name, path, full_path):
            self.id = id
            self.name = name
            self.path = path
            self.full_path = full_path
            self.description = "Test group"

    class Groups:
        def list(self, **kwargs):
            assert kwargs["get_all"] is False
            assert kwargs["page"] == 2
            assert kwargs["per_page"] == 25
            assert kwargs["search"] == "test"
            return [G(1, "group1", "group1", "group1"), G(2, "group2", "group2", "org/group2")]

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.groups = Groups()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    groups = client.get_groups(search="test", per_page=25, page=2)
    assert len(groups) == 2
    assert groups[0]["id"] == 1
    assert groups[1]["full_path"] == "org/group2"


def test_gitlab_client_get_groups_with_get_all(monkeypatch):
    """Test fetching all groups without pagination."""
    from app.core import gitlab_client as mod

    class G:
        def __init__(self, id, name):
            self.id = id
            self.name = name
            self.path = name
            self.full_path = name
            self.description = ""

    class Groups:
        def list(self, **kwargs):
            assert kwargs["get_all"] is True
            assert "page" not in kwargs
            return [G(i, f"group{i}") for i in range(5)]

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.groups = Groups()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    groups = client.get_groups(get_all=True)
    assert len(groups) == 5


def test_gitlab_client_get_groups_error(monkeypatch):
    """Test error handling when fetching groups fails."""
    from app.core import gitlab_client as mod

    class Groups:
        def list(self, **kwargs):
            raise RuntimeError("API error")

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.groups = Groups()

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to fetch groups"):
        client.get_groups()


def test_gitlab_client_get_project_mirrors(monkeypatch):
    """Test fetching mirrors for a project."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_get(self, path):
            assert path == "/projects/123/remote_mirrors"
            return [
                {
                    "id": 1,
                    "url": "https://mirror.com/repo.git",
                    "enabled": True,
                    "mirror_direction": "push",
                    "only_protected_branches": False,
                    "keep_divergent_refs": True,
                    "update_status": "finished",
                    "last_update_at": "2024-01-01T00:00:00Z",
                },
                {
                    "id": 2,
                    "url": "https://backup.com/repo.git",
                    "enabled": True,
                    "mirror_direction": "pull",
                }
            ]

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    mirrors = client.get_project_mirrors(123)
    assert len(mirrors) == 2
    assert mirrors[0]["id"] == 1
    assert mirrors[0]["mirror_direction"] == "push"
    assert mirrors[1]["id"] == 2


def test_gitlab_client_get_project_mirrors_empty(monkeypatch):
    """Test fetching mirrors when none exist."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_get(self, path):
            return []

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    mirrors = client.get_project_mirrors(123)
    assert mirrors == []


def test_gitlab_client_get_project_mirrors_error(monkeypatch):
    """Test error handling when fetching mirrors fails."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_get(self, path):
            raise RuntimeError("API error")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to fetch push mirrors"):
        client.get_project_mirrors(123)


def test_gitlab_client_trigger_mirror_update(monkeypatch):
    """Test triggering a mirror update."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.posted = []

        def http_post(self, path):
            self.posted.append(path)
            return {}

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    result = client.trigger_mirror_update(project_id=123, mirror_id=456)
    assert result is True
    assert client.gl.posted == ["/projects/123/remote_mirrors/456/sync"]


def test_gitlab_client_trigger_mirror_update_error(monkeypatch):
    """Test error handling when triggering mirror update fails."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_post(self, path):
            raise RuntimeError("API error")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to trigger push mirror update"):
        client.trigger_mirror_update(123, 456)


def test_gitlab_client_delete_mirror(monkeypatch):
    """Test deleting a mirror."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.deleted = []

        def http_delete(self, path):
            self.deleted.append(path)

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    result = client.delete_mirror(project_id=123, mirror_id=789)
    assert result is True
    assert client.gl.deleted == ["/projects/123/remote_mirrors/789"]


def test_gitlab_client_delete_mirror_error(monkeypatch):
    """Test error handling when deleting mirror fails with a non-404 error."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_delete(self, path):
            raise RuntimeError("Permission denied")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to delete push mirror"):
        client.delete_mirror(123, 789)


def test_gitlab_client_delete_mirror_not_found(monkeypatch):
    """Test that deleting an already-deleted mirror returns True (404 is acceptable)."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_delete(self, path):
            raise RuntimeError("404 Mirror not found")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    result = client.delete_mirror(123, 789)
    assert result is True


def test_gitlab_client_update_mirror(monkeypatch):
    """Test updating mirror settings."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.updates = []

        def http_put(self, path, post_data):
            self.updates.append((path, post_data))
            return {"id": 456, **post_data}

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    result = client.update_mirror(
        project_id=123,
        mirror_id=456,
        enabled=False,
        only_protected_branches=True,
        keep_divergent_refs=False
    )
    assert result["id"] == 456
    assert len(client.gl.updates) == 1
    path, data = client.gl.updates[0]
    assert path == "/projects/123/remote_mirrors/456"
    assert data["enabled"] is False
    assert data["only_protected_branches"] is True
    assert data["keep_divergent_refs"] is False


def test_gitlab_client_update_mirror_no_changes(monkeypatch):
    """Test updating mirror with no changes returns early."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            self.updates = []

        def http_put(self, path, post_data):
            self.updates.append((path, post_data))
            return {}

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    result = client.update_mirror(project_id=123, mirror_id=456)
    assert result == {"id": 456}
    assert len(client.gl.updates) == 0  # No API call made


def test_gitlab_client_update_mirror_error(monkeypatch):
    """Test error handling when updating mirror fails."""
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token, timeout=60):
            pass

        def http_put(self, path, post_data):
            raise RuntimeError("Validation error")

    class FakeGitlabModule:
        Gitlab = FakeGL

    monkeypatch.setattr(mod, "gitlab", FakeGitlabModule())
    monkeypatch.setattr(mod, "encryption", type("E", (), {"decrypt": lambda _s, x: "tok"})())

    client = mod.GitLabClient("https://example.com", "enc:any")
    with pytest.raises(Exception, match="Failed to update push mirror"):
        client.update_mirror(123, 456, enabled=False)



