import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_admin(guru_client, monkeypatch):
    # guru_client's user is lee@test.dev; put it on the admin allowlist
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_emails", ["lee@test.dev"])


async def test_non_admin_forbidden(guru_client):
    r = await guru_client.get("/api/admin/llm-config")
    assert r.status_code == 403


async def test_put_then_get_never_returns_key(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)
    r = await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-secret"})
    assert r.status_code == 200
    got = (await guru_client.get("/api/admin/llm-config")).json()
    assert got["provider"] == "openai" and got["advice_model"] == "gpt-4o"
    assert got["key_set"] is True
    assert "api_key" not in got and "sk-secret" not in str(got)


async def test_put_omitting_key_preserves_stored_key(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)
    await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-first"})
    # edit models, omit api_key -> key stays set
    await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4.1", "scan_model": "gpt-4o-mini"})
    got = (await guru_client.get("/api/admin/llm-config")).json()
    assert got["advice_model"] == "gpt-4.1" and got["key_set"] is True


async def test_test_endpoint_reports_failure_not_500(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("bad key")
    # force the test call to fail inside the provider
    import app.api.admin as admin_mod
    monkeypatch.setattr(admin_mod, "_run_test_call", boom)
    r = await guru_client.post("/api/admin/llm-config/test", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-bad"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


async def test_test_endpoint_never_leaks_key_in_detail(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)

    # provider error text that embeds the submitted key (Google puts the FULL key
    # in a ?key=... query param; OpenAI/Anthropic keys start with sk-).
    async def leaky(*a, **k):
        raise RuntimeError("auth failed for key=sk-supersecret123 (401)")
    import app.api.admin as admin_mod
    monkeypatch.setattr(admin_mod, "_run_test_call", leaky)
    r = await guru_client.post("/api/admin/llm-config/test", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-supersecret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    # the submitted key must not appear anywhere in the response
    assert "sk-supersecret123" not in str(body)


async def test_put_invalid_price_returns_422(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)
    r = await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-x", "advice_input_price": "abc"})
    assert r.status_code == 422
