"""Tests for the TLS keep-alive feature."""

import asyncio

import pytest

from app.core.tls_keepalive import TLSKeepAliveManager


class TestTLSKeepAliveManager:
    """Unit tests for TLSKeepAliveManager."""

    @pytest.mark.asyncio
    async def test_start_stop_no_instances(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        await manager.start([])
        assert manager.is_running
        assert manager.active_count == 0
        assert manager.get_status() == []
        await manager.stop()
        assert not manager.is_running

    @pytest.mark.asyncio
    async def test_start_with_instances(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        instances = [
            {"id": 1, "name": "Test GitLab", "url": "https://gitlab.example.com"},
            {"id": 2, "name": "Second GitLab", "url": "https://gitlab2.example.com:8443"},
        ]
        await manager.start(instances)
        assert manager.is_running
        assert manager.active_count == 2

        status = manager.get_status()
        assert len(status) == 2

        hosts = {s["host"] for s in status}
        assert "gitlab.example.com" in hosts
        assert "gitlab2.example.com" in hosts

        ports = {(s["host"], s["port"]) for s in status}
        assert ("gitlab.example.com", 443) in ports
        assert ("gitlab2.example.com", 8443) in ports

        await manager.stop()
        assert manager.active_count == 0

    @pytest.mark.asyncio
    async def test_get_instance_status(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        instances = [
            {"id": 1, "name": "Test GitLab", "url": "https://gitlab.example.com"},
        ]
        await manager.start(instances)

        status = manager.get_instance_status(1)
        assert status is not None
        assert status["instance_id"] == 1
        assert status["instance_name"] == "Test GitLab"
        assert status["host"] == "gitlab.example.com"
        assert status["port"] == 443

        assert manager.get_instance_status(999) is None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_refresh_adds_and_removes(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        await manager.start([
            {"id": 1, "name": "A", "url": "https://a.example.com"},
        ])
        assert manager.active_count == 1

        # Refresh: remove A, add B and C
        await manager.refresh([
            {"id": 2, "name": "B", "url": "https://b.example.com"},
            {"id": 3, "name": "C", "url": "https://c.example.com"},
        ])
        assert manager.active_count == 2
        assert manager.get_instance_status(1) is None
        assert manager.get_instance_status(2) is not None
        assert manager.get_instance_status(3) is not None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_start_already_started_warns(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        await manager.start([])
        # Starting again should not error
        await manager.start([])
        await manager.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        # Should not error
        await manager.stop()
        assert not manager.is_running

    @pytest.mark.asyncio
    async def test_invalid_url_skipped(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        # URL with no hostname should be skipped
        await manager.start([
            {"id": 1, "name": "Bad", "url": "not-a-url"},
        ])
        assert manager.active_count == 0
        await manager.stop()

    @pytest.mark.asyncio
    async def test_http_url_uses_port_80(self):
        manager = TLSKeepAliveManager(reconnect_interval=1)
        await manager.start([
            {"id": 1, "name": "HTTP", "url": "http://gitlab.example.com"},
        ])
        status = manager.get_instance_status(1)
        assert status is not None
        assert status["port"] == 80
        await manager.stop()


class TestTLSKeepAliveAPI:
    """API integration tests for TLS keep-alive endpoints."""

    @pytest.mark.asyncio
    async def test_create_instance_with_tls_keepalive(self, client, monkeypatch):
        """Creating an instance with tls_keepalive_enabled stores the field."""
        from tests.test_instances_api import FakeGitLabClient, patch_gitlab_client
        patch_gitlab_client(monkeypatch, FakeGitLabClient)
        FakeGitLabClient.test_ok = True

        payload = {
            "name": "keep-alive-test",
            "url": "https://gitlab.example.com",
            "token": "t1",
            "tls_keepalive_enabled": True,
        }
        resp = await client.post("/api/instances", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["tls_keepalive_enabled"] is True

    @pytest.mark.asyncio
    async def test_create_instance_default_tls_keepalive_false(self, client, monkeypatch):
        """By default, tls_keepalive_enabled is False."""
        from tests.test_instances_api import FakeGitLabClient, patch_gitlab_client
        patch_gitlab_client(monkeypatch, FakeGitLabClient)
        FakeGitLabClient.test_ok = True

        payload = {
            "name": "no-keepalive-test",
            "url": "https://gitlab.example.com",
            "token": "t1",
        }
        resp = await client.post("/api/instances", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["tls_keepalive_enabled"] is False

    @pytest.mark.asyncio
    async def test_update_instance_tls_keepalive(self, client, monkeypatch):
        """Updating tls_keepalive_enabled via PUT works."""
        from tests.test_instances_api import FakeGitLabClient, patch_gitlab_client
        patch_gitlab_client(monkeypatch, FakeGitLabClient)
        FakeGitLabClient.test_ok = True

        # Create instance
        resp = await client.post("/api/instances", json={
            "name": "update-test",
            "url": "https://gitlab.example.com",
            "token": "t1",
        })
        assert resp.status_code == 201
        instance_id = resp.json()["id"]
        assert resp.json()["tls_keepalive_enabled"] is False

        # Enable TLS keep-alive
        resp = await client.put(f"/api/instances/{instance_id}", json={
            "tls_keepalive_enabled": True,
        })
        assert resp.status_code == 200
        assert resp.json()["tls_keepalive_enabled"] is True

        # Disable TLS keep-alive
        resp = await client.put(f"/api/instances/{instance_id}", json={
            "tls_keepalive_enabled": False,
        })
        assert resp.status_code == 200
        assert resp.json()["tls_keepalive_enabled"] is False

    @pytest.mark.asyncio
    async def test_list_instances_includes_tls_keepalive(self, client, monkeypatch):
        """List instances includes tls_keepalive_enabled field."""
        from tests.test_instances_api import FakeGitLabClient, patch_gitlab_client
        patch_gitlab_client(monkeypatch, FakeGitLabClient)
        FakeGitLabClient.test_ok = True

        await client.post("/api/instances", json={
            "name": "list-test",
            "url": "https://gitlab.example.com",
            "token": "t1",
            "tls_keepalive_enabled": True,
        })

        resp = await client.get("/api/instances")
        assert resp.status_code == 200
        instances = resp.json()
        assert len(instances) == 1
        assert instances[0]["tls_keepalive_enabled"] is True

    @pytest.mark.asyncio
    async def test_tls_keepalive_status_endpoint(self, client):
        """TLS keep-alive status endpoint returns expected structure."""
        resp = await client.get("/api/instances/tls-keepalive/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "globally_enabled" in data
        assert "manager_running" in data
        assert "active_connections" in data
        assert "connections" in data
        assert isinstance(data["connections"], list)
