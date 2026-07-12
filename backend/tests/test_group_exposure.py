from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _hold(auth_client, symbol, qty, make_instrument):
    await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": symbol, "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": str(qty)})
    return pid


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
