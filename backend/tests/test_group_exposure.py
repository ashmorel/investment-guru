from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _hold(auth_client, symbol, qty, make_instrument, base_currency="GBP"):
    await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": symbol, "kind": "real", "base_currency": base_currency})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": str(qty)})
    return pid


def _stub_fx(monkeypatch, rates: dict):
    """Stub FxService.get_rate: base_currency -> rate into GBP (or a currency
    whose value is an Exception instance to simulate an FX-provider failure)."""
    from app.services.valuation import FxService

    async def fake(self, db, base, quote):
        assert quote == "GBP"
        r = rates.get(base)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise LookupError(base)
        return r

    monkeypatch.setattr(FxService, "get_rate", fake)


def _stub_valuation(monkeypatch, prices: dict):
    """Stub value_portfolio so exposure is deterministic (no live quotes).
    prices: symbol -> market_value_base (or None for unpriced).
    compute_group_exposure only reads summary.positions[].{symbol,
    market_value_base, day_change_base}, so a SimpleNamespace suffices — no need
    to construct the real PositionValuation/PortfolioSummary dataclasses. Patch
    the name imported INTO the exposure module (that's what the code calls)."""
    import types as _types

    import app.services.groups.exposure as expo

    async def fake(db, portfolio, quote_service, fx):
        positions = [
            _types.SimpleNamespace(
                symbol=p.instrument.symbol,
                market_value_base=prices.get(p.instrument.symbol),
                day_change_base=(None if prices.get(p.instrument.symbol) is None
                                 else Decimal("1")),
            )
            for p in portfolio.positions
        ]
        return _types.SimpleNamespace(positions=positions)

    monkeypatch.setattr(expo, "value_portfolio", fake)


async def test_exposure_groups_ungrouped_and_pct(auth_client, make_instrument, monkeypatch):
    await _hold(auth_client, "AAPL", 1, make_instrument)
    await _hold(auth_client, "XOM", 1, make_instrument)
    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "XOM": Decimal("30")})
    g = (await auth_client.post("/api/groups", json={"name": "Tech"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})

    body = (await auth_client.get("/api/groups/exposure")).json()
    assert body["total_base"] == "100.00"
    by = {(x["group_id"] or "ungrouped"): x for x in body["groups"]}
    assert by[g["id"]]["value_base"] == "70.00" and by[g["id"]]["pct"] == "70.00"
    assert by["ungrouped"]["value_base"] == "30.00" and by["ungrouped"]["name"] == "Ungrouped"


async def test_exposure_unpriced_degrades(auth_client, make_instrument, monkeypatch):
    await _hold(auth_client, "AAPL", 1, make_instrument)
    _stub_valuation(monkeypatch, {"AAPL": None})
    body = (await auth_client.get("/api/groups/exposure")).json()
    assert body["total_base"] == "0.00" and "AAPL" in body["unpriced"]


async def test_exposure_converts_foreign_portfolio_to_gbp(
        auth_client, make_instrument, monkeypatch):
    # GBP portfolio (rate=1, no fx needed) + USD portfolio (USD->GBP = 0.8).
    await _hold(auth_client, "AAPL", 1, make_instrument, base_currency="GBP")
    await _hold(auth_client, "XOM", 1, make_instrument, base_currency="USD")
    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "XOM": Decimal("30")})
    _stub_fx(monkeypatch, {"USD": Decimal("0.8")})
    g = (await auth_client.post("/api/groups", json={"name": "Energy"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "XOM", "group_id": g["id"]})

    body = (await auth_client.get("/api/groups/exposure")).json()
    by = {(x["group_id"] or "ungrouped"): x for x in body["groups"]}
    # XOM: 30 USD * 0.8 = 24.00 GBP; total: 70 (GBP) + 24 = 94.00 GBP.
    assert by[g["id"]]["value_base"] == "24.00"
    assert body["total_base"] == "94.00"
    assert by["ungrouped"]["value_base"] == "70.00"


async def test_exposure_fx_failure_degrades_portfolio(auth_client, make_instrument, monkeypatch):
    # USD portfolio whose FX rate can't be resolved -> its priced holdings degrade.
    await _hold(auth_client, "XOM", 1, make_instrument, base_currency="USD")
    _stub_valuation(monkeypatch, {"XOM": Decimal("30")})
    _stub_fx(monkeypatch, {"USD": LookupError("no fx")})
    body = (await auth_client.get("/api/groups/exposure")).json()
    assert body["total_base"] == "0.00"
    assert "XOM" in body["unpriced"]
    assert body["groups"] == []


async def test_exposure_portfolio_id_not_owned_404(auth_client, db_session):
    from app.core.security import hash_password
    from app.models import Portfolio
    from app.models.user import User

    other = User(email="other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    pf = Portfolio(user_id=other.id, name="Theirs", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.commit()

    resp = await auth_client.get(f"/api/groups/exposure?portfolio_id={pf.id}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "portfolio_not_found"
