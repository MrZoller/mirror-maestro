import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from app.config import settings
from app.core.auth import verify_credentials


def test_verify_credentials_auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert (
        verify_credentials(HTTPBasicCredentials(username="nope", password="bad"))
        == "authenticated"
    )


def test_verify_credentials_ok(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "secret")
    assert (
        verify_credentials(HTTPBasicCredentials(username="admin", password="secret"))
        == "admin"
    )


def test_verify_credentials_rejects_bad_password(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "secret")

    with pytest.raises(HTTPException) as exc:
        verify_credentials(HTTPBasicCredentials(username="admin", password="wrong"))

    assert exc.value.status_code == 401
    assert exc.value.headers.get("WWW-Authenticate") == "Basic"

