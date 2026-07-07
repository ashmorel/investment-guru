import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
