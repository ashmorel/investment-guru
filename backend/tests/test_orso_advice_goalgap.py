import pytest

from app.services.guru.schemas import OrsoAdvicePayload


def test_orso_advice_payload_has_contribution_suggestion():
    p = OrsoAdvicePayload(fund_verdicts=[], switch_plan=[], projection_comment="ok",
                          watch=[], disclaimer="not advice",
                          contribution_suggestion="Consider raising to HKD 40,000/mo.")
    assert p.contribution_suggestion.startswith("Consider")


@pytest.mark.asyncio(loop_scope="session")
async def test_context_includes_goal_gap(orso_client, db_session, monkeypatch):
    from decimal import Decimal

    from app.api.orso import get_orso_prices
    from app.services import valuation
    from app.services.orso.context import build_orso_context

    async def ident(self, db, base, quote):
        return Decimal("1")
    monkeypatch.setattr(valuation.FxService, "get_rate", ident)

    # goals so projection is populated
    await orso_client.put("/api/orso/goals", json={
        "birth_year": 1985, "retirement_target_age": 65,
        "retirement_target_pot": "5000000", "orso_monthly_contribution": "10000"})

    from sqlalchemy import select

    from app.models.user import User
    user = (await db_session.execute(select(User).where(
        User.email == "lee@test.dev"))).scalar_one()   # orso_client -> auth_client user
    fx = valuation.FxService(None)
    ctx = await build_orso_context(db_session, user, get_orso_prices(), fx)
    assert "goal_gap" in ctx
    assert "monthly_contribution" in ctx
