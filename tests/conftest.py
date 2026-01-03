import pytest

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import verify_credentials
from app.database import get_db
from app.models import Base


class FakeEncryption:
    _prefix = "enc:"

    def encrypt(self, data: str) -> str:
        return f"{self._prefix}{data}"

    def decrypt(self, encrypted_data: str) -> str:
        if not encrypted_data.startswith(self._prefix):
            raise ValueError("Invalid encrypted payload")
        return encrypted_data[len(self._prefix) :]

    def _initialize(self):
        """No-op for test environment - FakeEncryption doesn't need initialization."""
        pass


@pytest.fixture()
async def engine(tmp_path):
    db_file = tmp_path / "test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture()
async def session_maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
async def db_session(engine, session_maker: async_sessionmaker[AsyncSession]):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        yield session


@pytest.fixture()
async def app(engine, session_maker: async_sessionmaker[AsyncSession], monkeypatch):
    """
    FastAPI app with:
    - DB dependency overridden to use a per-test SQLite DB
    - Auth dependency overridden to bypass HTTP basic
    - Encryption swapped to a deterministic in-memory fake
    """
    # Track whether importing the app created ./data/encryption.key so we can
    # clean it up (tests shouldn't dirty the repo working tree).
    from pathlib import Path

    data_dir = Path("data")
    key_path = data_dir / "encryption.key"
    data_dir_existed = data_dir.exists()
    key_existed = key_path.exists()

    # Ensure a clean schema for each test that uses the app.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from app.main import app as fastapi_app

    fake_encryption = FakeEncryption()

    # Swap encryption used across modules to avoid filesystem key creation
    from app.api import instances as instances_mod
    from app.api import mirrors as mirrors_mod
    from app.api import backup as backup_mod
    from app.core import gitlab_client as gitlab_client_mod
    from app.core import mirror_gitlab_service as service_mod

    instances_mod.encryption = fake_encryption
    mirrors_mod.encryption = fake_encryption
    backup_mod.encryption = fake_encryption
    gitlab_client_mod.encryption = fake_encryption

    # Reset the mirror service singleton for each test
    service_mod.reset_mirror_gitlab_service()

    async def override_get_db():
        async with session_maker() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[verify_credentials] = lambda: "test-user"

    try:
        yield fastapi_app
    finally:
        fastapi_app.dependency_overrides.clear()
        # Best-effort cleanup of encryption artifacts created on import.
        try:
            if not key_existed and key_path.exists():
                key_path.unlink()
            if not data_dir_existed and data_dir.exists():
                # Remove dir only if empty.
                try:
                    data_dir.rmdir()
                except OSError:
                    pass
        except Exception:
            pass


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# -----------------------------------------------------------------------------
# E2E Test Fixtures
# -----------------------------------------------------------------------------


def _env(name: str) -> str | None:
    """Get environment variable, stripping whitespace."""
    import os

    v = os.getenv(name)
    return v.strip() if v and v.strip() else None


def _should_run_live() -> bool:
    """Check if live GitLab tests are enabled."""
    return (_env("E2E_LIVE_GITLAB") or "").lower() in {"1", "true", "yes", "on"}


@pytest.fixture
def e2e_config_single():
    """
    Configuration for single-instance E2E tests.

    Requires:
    - E2E_LIVE_GITLAB=1 (opt-in guard)
    - E2E_GITLAB_URL
    - E2E_GITLAB_TOKEN
    - E2E_GITLAB_GROUP_PATH
    """
    if not _should_run_live():
        pytest.skip("Live GitLab E2E is opt-in. Set E2E_LIVE_GITLAB=1 to run.")

    url = _env("E2E_GITLAB_URL")
    token = _env("E2E_GITLAB_TOKEN")
    group_path = _env("E2E_GITLAB_GROUP_PATH")

    missing = []
    if not url:
        missing.append("E2E_GITLAB_URL")
    if not token:
        missing.append("E2E_GITLAB_TOKEN")
    if not group_path:
        missing.append("E2E_GITLAB_GROUP_PATH")

    if missing:
        pytest.skip(f"Single-instance E2E requires: {', '.join(missing)}")

    return {
        "url": url,
        "token": token,
        "group_path": group_path,
        "http_username": _env("E2E_GITLAB_HTTP_USERNAME") or "oauth2",
        "mirror_timeout_s": float(_env("E2E_GITLAB_MIRROR_TIMEOUT_S") or "120"),
    }


@pytest.fixture
def e2e_config_dual():
    """
    Configuration for dual-instance E2E tests.

    Requires:
    - E2E_LIVE_GITLAB=1 (opt-in guard)
    - E2E_GITLAB_URL, E2E_GITLAB_TOKEN, E2E_GITLAB_GROUP_PATH (instance 1)
    - E2E_GITLAB_URL_2, E2E_GITLAB_TOKEN_2, E2E_GITLAB_GROUP_PATH_2 (instance 2)
    """
    if not _should_run_live():
        pytest.skip("Live GitLab E2E is opt-in. Set E2E_LIVE_GITLAB=1 to run.")

    # Instance 1
    url1 = _env("E2E_GITLAB_URL")
    token1 = _env("E2E_GITLAB_TOKEN")
    group1 = _env("E2E_GITLAB_GROUP_PATH")

    # Instance 2
    url2 = _env("E2E_GITLAB_URL_2")
    token2 = _env("E2E_GITLAB_TOKEN_2")
    group2 = _env("E2E_GITLAB_GROUP_PATH_2")

    missing = []
    if not url1:
        missing.append("E2E_GITLAB_URL")
    if not token1:
        missing.append("E2E_GITLAB_TOKEN")
    if not group1:
        missing.append("E2E_GITLAB_GROUP_PATH")
    if not url2:
        missing.append("E2E_GITLAB_URL_2")
    if not token2:
        missing.append("E2E_GITLAB_TOKEN_2")
    if not group2:
        missing.append("E2E_GITLAB_GROUP_PATH_2")

    if missing:
        pytest.skip(f"Dual-instance E2E requires: {', '.join(missing)}")

    http_username = _env("E2E_GITLAB_HTTP_USERNAME") or "oauth2"
    timeout = float(_env("E2E_GITLAB_MIRROR_TIMEOUT_S") or "120")

    return {
        "instance1": {
            "url": url1,
            "token": token1,
            "group_path": group1,
            "http_username": http_username,
        },
        "instance2": {
            "url": url2,
            "token": token2,
            "group_path": group2,
            "http_username": http_username,
        },
        "mirror_timeout_s": timeout,
    }


@pytest.fixture
def resource_tracker():
    """Provides a ResourceTracker for test cleanup."""
    from tests.e2e_helpers import ResourceTracker

    return ResourceTracker()

