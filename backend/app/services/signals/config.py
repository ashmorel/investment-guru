from datetime import timedelta
from decimal import Decimal

EARNINGS_DAYS = 7
EARNINGS_HIGH_DAYS = 2
DAY_MOVE_PCT = Decimal("5")
DAY_MOVE_HIGH_PCT = Decimal("10")
WEEK_MOVE_PCT = Decimal("10")
WEEK_MOVE_HIGH_PCT = Decimal("20")
FIFTY_TWO_NEAR_PCT = Decimal("2")
VOLUME_MULT = Decimal("2")
VOLUME_HIGH_MULT = Decimal("3")
CONC_NAME_PCT = Decimal("20")
CONC_NAME_HIGH_PCT = Decimal("30")
CONC_SECTOR_PCT = Decimal("40")
CONC_SECTOR_HIGH_PCT = Decimal("55")
FX_PCT = Decimal("30")
FX_HIGH_PCT = Decimal("50")
NEWS_WINDOW = timedelta(hours=48)
