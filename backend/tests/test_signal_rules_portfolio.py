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


def _name_drafts(out):
    return [s for s in out if s.data.get("scope") == "name"]


def _sector_drafts(out):
    return [s for s in out if s.data.get("scope") == "sector"]


def test_single_name_boundary_watch_high_and_skip():
    insts = [_Inst(1, "AAPL", "Tech"), _Inst(2, "MSFT", "Fin"), _Inst(3, "TSLA", "Auto")]
    # AAPL exactly 20% -> watch (fires at CONC_NAME_PCT); MSFT exactly 30% -> high;
    # TSLA 19.99% (< 20) -> no name signal. total 1000.
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("1000")},
        positions=[
            _PV("AAPL", Decimal("200")),   # 20.00%
            _PV("MSFT", Decimal("300")),   # 30.00%
            _PV("TSLA", Decimal("199.9")), # 19.99%
        ],
    )
    drafts = _name_drafts(concentration(_ctx(summary, insts)))
    names = {s.data["symbol"]: s.severity for s in drafts}
    assert names == {"AAPL": "watch", "MSFT": "high"}  # TSLA absent (below threshold)


def test_sector_boundary_watch_and_high():
    # Tech = 40% exactly -> watch; Fin = 55% exactly -> high; total 1000.
    insts = [
        _Inst(1, "A", "Tech"), _Inst(2, "B", "Fin"),
        _Inst(3, "C", "Fin"), _Inst(4, "D", "Misc"),
    ]
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("1000")},
        positions=[
            _PV("A", Decimal("400")),   # Tech 40%
            _PV("B", Decimal("300")),   # Fin 55%
            _PV("C", Decimal("250")),
            _PV("D", Decimal("50")),    # Misc 5% -> no sector signal
        ],
    )
    secs = {s.data["sector"]: s.severity for s in _sector_drafts(concentration(_ctx(summary, insts)))}
    assert secs == {"Tech": "watch", "Fin": "high"}


def test_none_market_value_skipped_and_unclassified_sector():
    inst = _Inst(1, "X", None)  # sector None -> "Unclassified"
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("1000")},
        positions=[_PV("X", None)],  # unpriced -> skipped, no crash
    )
    assert concentration(_ctx(summary, [inst])) == []


def test_fx_boundary_watch_high_and_zero_total_guard():
    # USD exactly 30% -> watch; HKD exactly 50% -> high; base GBP skipped.
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("200"), "USD": Decimal("300"), "HKD": Decimal("500")},
        positions=[],
    )
    fx_drafts = fx_exposure(_ctx(summary, []))
    fx = {s.data["currency"]: s.severity for s in fx_drafts}
    assert fx == {"USD": "watch", "HKD": "high"}
    # zero total_value never divides
    zero = _Summary(total_value=Decimal("0"), currency_exposure={"USD": Decimal("0")}, positions=[])
    assert fx_exposure(_ctx(zero, [])) == []
    assert concentration(_ctx(zero, [])) == []
