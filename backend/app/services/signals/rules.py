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
