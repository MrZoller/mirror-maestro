import pytest


def test_gitlab_client_test_connection_true(monkeypatch):
    from app.core import gitlab_client as mod

    class FakeGL:
        def __init__(self, url, private_token):
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
        def __init__(self, url, private_token):
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
            assert kwargs["get_all"] is True
            return [P(1, "n", "p", "g/p")]

    class FakeGL:
        def __init__(self, url, private_token):
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

