from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.market_data.base import Quote
from app.services.signals.rules import (
    earnings_upcoming,
    fifty_two_week,
    news_recent,
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


def test_price_move_day_exact_boundaries():
    inst = _Inst(1, "AAPL")
    # exactly -5% -> watch (boundary is inclusive: abs(pct) < PCT is required to skip)
    ctx_watch = _ctx(instruments=[inst], quotes={"AAPL": _q(95, 100)})
    out_watch = price_move_day(ctx_watch)
    assert len(out_watch) == 1 and out_watch[0].severity == "watch"

    # exactly -10% -> high
    ctx_high = _ctx(instruments=[inst], quotes={"AAPL": _q(90, 100)})
    out_high = price_move_day(ctx_high)
    assert len(out_high) == 1 and out_high[0].severity == "high"

    # -4.5% (just under the 5% floor) -> no fire
    ctx_none = _ctx(instruments=[inst], quotes={"AAPL": _q(95.5, 100)})
    assert price_move_day(ctx_none) == []


def test_earnings_upcoming_exact_boundaries():
    inst = _Inst(1, "NVDA")
    today = date(2026, 7, 7)

    # exactly 2 days out -> high (days <= EARNINGS_HIGH_DAYS)
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 9)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "high"

    # exactly 3 days out -> watch
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 10)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "watch"

    # exactly 7 days out (EARNINGS_DAYS boundary) -> watch
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 14)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "watch"

    # 8 days out -> no fire
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 15)})
    assert earnings_upcoming(ctx) == []

    # today (0 days) -> high
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 7)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "high"

    # past date -> no fire
    ctx = _ctx(instruments=[inst], today=today, earnings={1: date(2026, 7, 6)})
    assert earnings_upcoming(ctx) == []


def test_unusual_volume_exact_boundaries():
    inst = _Inst(1, "AAPL")
    base_bars = [_Bar(date(2026, 7, i + 1), 100, 10_000_000) for i in range(30)]

    # exactly 2x average -> watch
    bars_watch = base_bars + [_Bar(date(2026, 7, 31), 100, 20_000_000)]
    ctx_watch = _ctx(instruments=[inst], bars={1: bars_watch})
    out_watch = unusual_volume(ctx_watch)
    assert len(out_watch) == 1 and out_watch[0].severity == "watch"

    # exactly 3x average -> high
    bars_high = base_bars + [_Bar(date(2026, 7, 31), 100, 30_000_000)]
    ctx_high = _ctx(instruments=[inst], bars={1: bars_high})
    out_high = unusual_volume(ctx_high)
    assert len(out_high) == 1 and out_high[0].severity == "high"

    # 1.9x average -> no fire
    bars_none = base_bars + [_Bar(date(2026, 7, 31), 100, 19_000_000)]
    ctx_none = _ctx(instruments=[inst], bars={1: bars_none})
    assert unusual_volume(ctx_none) == []


def test_fifty_two_week_boundaries():
    inst = _Inst(1, "AAPL")
    # single bar sets the 52-week range directly: low=90, high=110
    bars = [_Bar(date(2026, 7, 1), 100, 1_000_000, high=110, low=90)]

    # price at the range high -> high (new 52-week high)
    ctx_high = _ctx(instruments=[inst], bars={1: bars}, quotes={"AAPL": _q(110, 100)})
    out_high = fifty_two_week(ctx_high)
    assert len(out_high) == 1 and out_high[0].severity == "high"
    assert "high" in out_high[0].title

    # price within 2% of the high (108 is ~1.82% below 110) -> watch
    ctx_near = _ctx(instruments=[inst], bars={1: bars}, quotes={"AAPL": _q(108, 100)})
    out_near = fifty_two_week(ctx_near)
    assert len(out_near) == 1 and out_near[0].severity == "watch"
    assert "high" in out_near[0].title

    # price comfortably mid-range (100, ~9% off the high, ~11% off the low) -> no fire
    ctx_mid = _ctx(instruments=[inst], bars={1: bars}, quotes={"AAPL": _q(100, 100)})
    assert fifty_two_week(ctx_mid) == []

    # price at the range low -> high (new 52-week low)
    ctx_low = _ctx(instruments=[inst], bars={1: bars}, quotes={"AAPL": _q(90, 100)})
    out_low = fifty_two_week(ctx_low)
    assert len(out_low) == 1 and out_low[0].severity == "high"
    assert "low" in out_low[0].title


def test_news_recent_counts_and_no_fire_when_empty():
    inst = _Inst(1, "AAPL")
    items = [
        SimpleNamespace(title="Apple beats earnings estimates", url="http://example.com/a"),
        SimpleNamespace(title="Apple launches new product", url="http://example.com/b"),
    ]
    ctx = _ctx(instruments=[inst], news={1: items})
    out = news_recent(ctx)
    assert len(out) == 1
    sig = out[0]
    assert sig.severity == "info"
    assert sig.instrument_id == 1
    assert sig.data["count"] == "2"
    assert sig.data["url"] == "http://example.com/a"
    assert "Apple beats earnings estimates" in sig.title

    inst_empty = _Inst(2, "MSFT")
    ctx_empty = _ctx(instruments=[inst_empty], news={2: []})
    assert news_recent(ctx_empty) == []
