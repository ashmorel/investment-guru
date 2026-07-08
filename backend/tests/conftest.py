from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.instruments import get_provider
from app.api.valuation import get_services
from app.core.db import Base, get_session
from app.main import create_app
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService

TEST_DATABASE_URL = "postgresql+asyncpg://guru:guru@localhost:5433/guru_test"

test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


class _NullProvider:
    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise LookupError("no fx in tests")

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return None


class _NullNewsProvider:
    async def get_news(self, symbol):
        return []


def _test_services():
    provider = _NullProvider()
    return QuoteService(provider), FxService(provider)


def _null_analyzer():
    from app.services.market_data.fundamentals import FundamentalsService
    from app.services.market_data.history import HistoryService
    from app.services.market_data.news import NewsService
    from app.services.market_data.quotes import QuoteService
    from app.services.signals.engine import SignalEngine
    from app.services.valuation import FxService

    provider = _NullProvider()
    qs = QuoteService(provider)
    return SignalEngine(
        quotes=qs, fx=FxService(provider), history=HistoryService(provider),
        fundamentals=FundamentalsService(provider), news=NewsService(_NullNewsProvider()),
        provider=provider,
    )


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
    app.dependency_overrides[get_services] = _test_services
    app.dependency_overrides[get_provider] = lambda: _NullProvider()
    from app.api.signals import get_analyzer
    app.dependency_overrides[get_analyzer] = _null_analyzer
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # tests that need a fake provider swap it via c.app
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


from app.models import Instrument  # noqa: E402


async def _make_instrument(db_session, symbol: str, **overrides) -> Instrument:
    defaults = dict(
        symbol=symbol, name=f"{symbol} Co", exchange="NMS", market="US", currency="USD"
    )
    inst = Instrument(**{**defaults, **overrides})
    db_session.add(inst)
    await db_session.commit()
    return inst


@pytest_asyncio.fixture
def make_instrument(db_session):
    async def _factory(symbol: str, **overrides) -> Instrument:
        return await _make_instrument(db_session, symbol, **overrides)

    return _factory


@pytest_asyncio.fixture
def fake_llm():
    from app.services.guru.llm.fake import FakeLLMProvider

    return FakeLLMProvider()


@pytest_asyncio.fixture
async def guru_client(auth_client, fake_llm) -> httpx.AsyncClient:
    from app.api.guru import get_guru
    from app.services.guru.service import GuruService

    svc = GuruService(fake_llm, *(_test_services()))
    auth_client.app.dependency_overrides[get_guru] = lambda: svc
    auth_client.fake_llm = fake_llm
    return auth_client
