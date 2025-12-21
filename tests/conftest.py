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
    from app.api import tokens as tokens_mod
    from app.core import gitlab_client as gitlab_client_mod

    instances_mod.encryption = fake_encryption
    mirrors_mod.encryption = fake_encryption
    tokens_mod.encryption = fake_encryption
    gitlab_client_mod.encryption = fake_encryption

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

