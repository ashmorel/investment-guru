from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _fund_with_price(orso_client, db_session, code, currency, units, price):
    from datetime import date
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": code, "name": code, "asset_class": "equity", "risk_rating": 4,
        "currency": currency,
    })).json()["id"]
    await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": fid, "units": str(units), "contribution_pct": "100"}]})
    await orso_client.put("/api/orso/prices/manual", json={
        "fund_id": fid, "price": str(price), "as_of": date.today().isoformat()})
    return fid


async def test_overview_converts_each_fund_to_display_currency(
        orso_client, db_session, monkeypatch):
    # Stub FX: HKD->GBP = 0.1, USD->GBP = 0.8, identity otherwise.
    from app.services import valuation

    async def fake_rate(self, db, base, quote):
        table = {("HKD", "GBP"): Decimal("0.1"), ("USD", "GBP"): Decimal("0.8"),
                 ("HKD", "HKD"): Decimal("1"), ("USD", "HKD"): Decimal("8")}
        if base == quote:
            return Decimal("1")
        return table[(base, quote)]
    monkeypatch.setattr(valuation.FxService, "get_rate", fake_rate)

    await _fund_with_price(orso_client, db_session, "HKEQ", "HKD", 100, 10)   # 1000 HKD
    r = await orso_client.get("/api/orso/overview")
    body = r.json()
    assert body["display_currency"] == "GBP"
    row = next(f for f in body["funds"] if f["code"] == "HKEQ")
    assert row["value_native"] == "1000.00"
    assert row["currency"] == "HKD"
    assert row["value_display"] == "100.00"          # 1000 * 0.1
    assert body["total_display"] == "100.00"


async def test_overview_fx_failure_degrades_not_500(orso_client, db_session, monkeypatch):
    from app.services import valuation

    async def boom(self, db, base, quote):
        if base == quote:
            return Decimal("1")
        raise RuntimeError("fx down")
    monkeypatch.setattr(valuation.FxService, "get_rate", boom)

    await _fund_with_price(orso_client, db_session, "USEQ", "USD", 10, 50)   # 500 USD
    r = await orso_client.get("/api/orso/overview")
    assert r.status_code == 200
    body = r.json()
    row = next(f for f in body["funds"] if f["code"] == "USEQ")
    assert row["value_native"] == "500.00"
    assert row["value_display"] is None
    assert "USEQ" in body["flags"]["fx_unavailable"]


async def test_put_display_currency_persists_and_recomputes(orso_client, db_session, monkeypatch):
    from app.services import valuation

    async def fake_rate(self, db, base, quote):
        if base == quote:
            return Decimal("1")
        return {("HKD", "USD"): Decimal("0.128")}[(base, quote)]
    monkeypatch.setattr(valuation.FxService, "get_rate", fake_rate)

    await _fund_with_price(orso_client, db_session, "HKEQ2", "HKD", 100, 10)  # 1000 HKD
    r = await orso_client.put("/api/orso/display-currency", json={"currency": "usd"})
    assert r.status_code == 200 and r.json()["currency"] == "USD"
    body = (await orso_client.get("/api/orso/overview")).json()
    assert body["display_currency"] == "USD"
    row = next(f for f in body["funds"] if f["code"] == "HKEQ2")
    assert row["value_display"] == "128.00"          # 1000 * 0.128
