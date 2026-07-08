from decimal import ROUND_HALF_UP, Decimal

from app.services.market_data.history import avg_volume, fifty_two_week_range, period_return
from app.services.signals import config
from app.services.signals.types import SignalContext, SignalDraft

TWO_DP = Decimal("0.01")


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def earnings_upcoming(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        ed = ctx.earnings.get(inst.id)
        if ed is None:
            continue
        days = (ed - ctx.today).days
        if days < 0 or days > config.EARNINGS_DAYS:
            continue
        sev = "high" if days <= config.EARNINGS_HIGH_DAYS else "watch"
        when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        out.append(SignalDraft(
            kind="earnings_upcoming", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} reports {when}",
            detail=f"Earnings on {ed.isoformat()}",
            data={"date": ed.isoformat(), "days_until": str(days)},
        ))
    return out


def price_move_day(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        q = ctx.quotes.get(inst.symbol)
        if q is None or q.previous_close is None or q.previous_close == 0:
            continue
        pct = _round((q.price - q.previous_close) / q.previous_close * 100)
        if abs(pct) < config.DAY_MOVE_PCT:
            continue
        sev = "high" if abs(pct) >= config.DAY_MOVE_HIGH_PCT else "watch"
        arrow = "up" if pct > 0 else "down"
        out.append(SignalDraft(
            kind="price_move_day", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} {arrow} {abs(pct)}% today",
            detail=f"Day move {pct}% (last {q.price})",
            data={"pct": str(pct), "close": str(q.price)},
        ))
    return out


def price_move_week(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        r = period_return(bars, 5)
        if r is None or abs(r) < config.WEEK_MOVE_PCT:
            continue
        sev = "high" if abs(r) >= config.WEEK_MOVE_HIGH_PCT else "watch"
        arrow = "up" if r > 0 else "down"
        out.append(SignalDraft(
            kind="price_move_week", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} {arrow} {abs(r)}% this week",
            detail=f"5-day return {r}%",
            data={"pct": str(r)},
        ))
    return out


def fifty_two_week(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        rng = fifty_two_week_range(bars)
        q = ctx.quotes.get(inst.symbol)
        if rng is None or q is None:
            continue
        low, high = rng
        price = q.price
        near_high = high > 0 and (high - price) / high * 100 <= config.FIFTY_TWO_NEAR_PCT
        near_low = low > 0 and (price - low) / low * 100 <= config.FIFTY_TWO_NEAR_PCT
        if price >= high or (near_high and price > 0):
            sev = "high" if price >= high else "watch"
            out.append(SignalDraft(
                kind="fifty_two_week", severity=sev, instrument_id=inst.id,
                title=f"{inst.symbol} near 52-week high",
                detail=f"Price {price} vs 52w high {high}",
                data={"price": str(price), "high": str(high), "low": str(low)},
            ))
        elif price <= low or near_low:
            sev = "high" if price <= low else "watch"
            out.append(SignalDraft(
                kind="fifty_two_week", severity=sev, instrument_id=inst.id,
                title=f"{inst.symbol} near 52-week low",
                detail=f"Price {price} vs 52w low {low}",
                data={"price": str(price), "high": str(high), "low": str(low)},
            ))
    return out


def unusual_volume(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        if len(bars) < 2 or bars[-1].volume is None:
            continue
        avg = avg_volume(bars[:-1], 30)
        if avg is None or avg == 0:
            continue
        mult = _round(Decimal(bars[-1].volume) / avg)
        if mult < config.VOLUME_MULT:
            continue
        sev = "high" if mult >= config.VOLUME_HIGH_MULT else "watch"
        out.append(SignalDraft(
            kind="unusual_volume", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} volume {mult}x average",
            detail=f"Today {bars[-1].volume:,} vs avg {int(avg):,}",
            data={"mult": str(mult), "volume": str(bars[-1].volume)},
        ))
    return out


def news_recent(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        items = ctx.news.get(inst.id) or []
        if not items:
            continue
        top = items[0]
        out.append(SignalDraft(
            kind="news_recent", severity="info", instrument_id=inst.id,
            title=f"{inst.symbol}: {top.title}",
            detail=f"{len(items)} recent headline{'s' if len(items) != 1 else ''}",
            data={"count": str(len(items)), "url": top.url},
        ))
    return out


PER_INSTRUMENT_RULES = [
    earnings_upcoming, price_move_day, price_move_week,
    fifty_two_week, unusual_volume, news_recent,
]


def concentration(ctx: SignalContext) -> list[SignalDraft]:
    s = ctx.summary
    if s is None or not s.total_value or s.total_value == 0:
        return []
    total = s.total_value
    sector_by_symbol = {i.symbol: (i.sector or "Unclassified") for i in ctx.instruments}
    out = []
    # single-name
    for pv in s.positions:
        if pv.market_value_base is None:
            continue
        pct = _round(pv.market_value_base / total * 100)
        if pct < config.CONC_NAME_PCT:
            continue
        sev = "high" if pct >= config.CONC_NAME_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="concentration", severity=sev, instrument_id=None,
            title=f"{pv.symbol} is {pct}% of portfolio",
            detail=f"Single-name concentration {pct}%",
            data={"symbol": pv.symbol, "pct": str(pct), "scope": "name"},
        ))
    # sector
    sector_totals: dict[str, Decimal] = {}
    for pv in s.positions:
        if pv.market_value_base is None:
            continue
        sec = sector_by_symbol.get(pv.symbol, "Unclassified")
        sector_totals[sec] = sector_totals.get(sec, Decimal("0")) + pv.market_value_base
    for sec, val in sector_totals.items():
        pct = _round(val / total * 100)
        if pct < config.CONC_SECTOR_PCT:
            continue
        sev = "high" if pct >= config.CONC_SECTOR_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="concentration", severity=sev, instrument_id=None,
            title=f"{sec} sector is {pct}% of portfolio",
            detail=f"Sector concentration {pct}%",
            data={"sector": sec, "pct": str(pct), "scope": "sector"},
        ))
    return out


def fx_exposure(ctx: SignalContext) -> list[SignalDraft]:
    s = ctx.summary
    if s is None or not s.total_value or s.total_value == 0:
        return []
    base = ctx.portfolio.base_currency
    out = []
    for ccy, val in s.currency_exposure.items():
        if ccy == base:
            continue
        pct = _round(val / s.total_value * 100)
        if pct < config.FX_PCT:
            continue
        sev = "high" if pct >= config.FX_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="fx_exposure", severity=sev, instrument_id=None,
            title=f"{pct}% exposure to {ccy}",
            detail=f"Non-base ({base}) currency exposure to {ccy}",
            data={"currency": ccy, "pct": str(pct)},
        ))
    return out


PORTFOLIO_RULES = [concentration, fx_exposure]
ALL_RULES = PER_INSTRUMENT_RULES + PORTFOLIO_RULES
