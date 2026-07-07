from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_session
from app.main import create_app

TEST_DATABASE_URL = "postgresql+asyncpg://guru:guru@localhost:5433/guru_test"

test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema():
    import app.models  # noqa: F401  (register all models)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables(_create_schema):
    yield
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


from app.core.security import hash_password  # noqa: E402
from app.models.user import User  # noqa: E402


@pytest_asyncio.fixture
async def auth_client(client, db_session) -> httpx.AsyncClient:
    user = User(email="lee@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": "lee@test.dev", "password": "pw123456"}
    )
    assert resp.status_code == 204
    return client
