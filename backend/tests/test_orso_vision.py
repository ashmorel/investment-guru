import base64

import pytest

from app.services.guru.schemas import ExtractedFundRow, OrsoStatementExtraction

pytestmark = pytest.mark.asyncio(loop_scope="session")

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


async def test_screenshot_returns_draft(orso_client, fake_llm):
    fake_llm.structured_queue.append(OrsoStatementExtraction(rows=[
        ExtractedFundRow(fund_code="HKEQ", fund_name="HK Equity", units="100",
                         value="1000", currency="HKD", contribution_pct="100")]))
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "screenshot"
    assert body["rows"][0]["parsed_code"] == "HKEQ"
    assert body["rows"][0]["implied_price"] == "10.0000"
    # the image block was actually sent to the provider
    call = fake_llm.calls[-1]
    blocks = call["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in blocks)


async def test_screenshot_budget_exhausted_429(orso_client, fake_llm, db_session, monkeypatch):
    async def over(db, user_id, *, now=None):
        from app.services.guru.budget import BudgetExhausted
        raise BudgetExhausted()
    monkeypatch.setattr("app.services.orso.vision.check_budget", over)
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 429 and r.json()["detail"] == "budget_exhausted"


async def test_screenshot_llm_failure_502_not_500(orso_client, fake_llm):
    fake_llm.fail_structured = 1
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 502 and r.json()["detail"] == "llm_error"


async def test_screenshot_rejects_non_image_415(orso_client):
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415
