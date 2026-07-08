from dataclasses import dataclass
from decimal import Decimal

from app.services.signals.rules import concentration, fx_exposure
from app.services.signals.types import SignalContext


@dataclass
class _PV:
    symbol: str
    market_value_base: Decimal | None


@dataclass
class _Summary:
    total_value: Decimal | None
    currency_exposure: dict
    positions: list


class _Inst:
    def __init__(self, id, symbol, sector, currency="USD"):
        self.id, self.symbol, self.sector, self.currency = id, symbol, sector, currency
        self.name = symbol


class _PF:
    base_currency = "GBP"


def _ctx(summary, instruments):
    return SignalContext(
        portfolio=_PF(), summary=summary, quotes={}, bars={}, earnings={}, news={},
        instruments=instruments, today=None,
    )


def test_single_name_concentration_high():
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("1000")},
        positions=[_PV("AAPL", Decimal("320")), _PV("MSFT", Decimal("680"))],
    )
    insts = [_Inst(1, "AAPL", "Tech"), _Inst(2, "MSFT", "Tech")]
    out = concentration(_ctx(summary, insts))
    # AAPL 32% -> high single-name; sector Tech 100% -> high sector
    kinds = [(s.kind, s.severity) for s in out]
    assert ("concentration", "high") in kinds


def test_fx_exposure_fires_on_non_base():
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("400"), "USD": Decimal("600")},
        positions=[],
    )
    out = fx_exposure(_ctx(summary, []))
    assert out and out[0].severity == "high" and "USD" in out[0].title


def test_no_summary_no_fire():
    assert concentration(_ctx(None, [])) == []
    assert fx_exposure(_ctx(None, [])) == []
