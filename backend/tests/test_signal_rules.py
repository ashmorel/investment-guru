from datetime import UTC, date, datetime
from decimal import Decimal

from app.services.market_data.base import Quote
from app.services.signals.rules import (
    earnings_upcoming,
    price_move_day,
    price_move_week,
    unusual_volume,
)
from app.services.signals.types import SignalContext


class _Inst:
    def __init__(self, id, symbol, sector="Tech", currency="USD"):
        self.id, self.symbol, self.sector, self.currency = id, symbol, sector, currency
        self.name = symbol


class _Bar:
    def __init__(self, d, close, volume, high=None, low=None):
        self.date, self.close, self.volume = d, Decimal(str(close)), volume
        self.high = Decimal(str(high if high is not None else close))
        self.low = Decimal(str(low if low is not None else close))


def _ctx(**kw):
    base = dict(
        portfolio=None, summary=None, quotes={}, bars={}, earnings={}, news={},
        instruments=[], today=date(2026, 7, 7),
    )
    base.update(kw)
    return SignalContext(**base)


def _q(price, prev):
    return Quote(symbol="X", price=Decimal(str(price)), currency="USD",
                 previous_close=Decimal(str(prev)), as_of=datetime.now(UTC))


def test_earnings_within_window_fires_high():
    inst = _Inst(1, "NVDA")
    ctx = _ctx(instruments=[inst], earnings={1: date(2026, 7, 8)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "high" and out[0].instrument_id == 1


def test_earnings_far_out_no_fire():
    inst = _Inst(1, "NVDA")
    ctx = _ctx(instruments=[inst], earnings={1: date(2026, 9, 1)})
    assert earnings_upcoming(ctx) == []


def test_price_move_day_watch_and_high():
    inst = _Inst(1, "AAPL")
    ctx = _ctx(instruments=[inst], quotes={"AAPL": _q(94, 100)})  # -6%
    out = price_move_day(ctx)
    assert out[0].severity == "watch"
    ctx2 = _ctx(instruments=[inst], quotes={"AAPL": _q(88, 100)})  # -12%
    assert price_move_day(ctx2)[0].severity == "high"


def test_price_move_week_needs_bars():
    inst = _Inst(1, "AAPL")
    # no history for this instrument → week rule can't fire
    ctx = _ctx(instruments=[inst], bars={1: []})
    assert price_move_week(ctx) == []


def test_unusual_volume_fires():
    inst = _Inst(1, "AAPL")
    bars = [_Bar(date(2026, 7, i + 1), 100, 10_000_000) for i in range(30)]
    bars.append(_Bar(date(2026, 7, 31), 100, 40_000_000))  # 4x avg
    ctx = _ctx(instruments=[inst], bars={1: bars})
    out = unusual_volume(ctx)
    assert out and out[0].severity == "high"
