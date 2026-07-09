from decimal import Decimal

from app.services.orso.projection import project


def test_projection_hand_computed_5pct():
    """Test projection with 5% annual rate over 10 years.

    Computed values (verified with Python Decimal):
    - pot: 1,000,000 @ 5% for 10 years with 15,000/mo contribution
    - fv_pot = 1,647,009.50 (principal growth)
    - fv_contrib = 2,329,234.19 (contribution annuity)
    - total = 3,976,243.69
    """
    [s2, s5, s8] = project(Decimal("1000000"), Decimal("15000"), 10, Decimal("4000000"))
    assert s5.rate == Decimal("0.05")
    assert abs(s5.projected_pot - Decimal("3976243.69")) < Decimal("1.00")
    assert s5.on_track is False and s5.gap < 0
    assert s8.on_track is True and s8.gap > 0


def test_projection_zero_contribution_and_no_target():
    """Test projection with no monthly contribution and no target (on_track/gap should be None).

    Computed values (verified with Python Decimal):
    - pot: 100,000 @ 2% for 5 years with 0/mo contribution
    - fv = 110,507.89
    """
    [s2, _, _] = project(Decimal("100000"), Decimal("0"), 5, None)
    assert abs(s2.projected_pot - Decimal("110507.89")) < Decimal("1.00")
    assert s2.on_track is None and s2.gap is None


def test_projection_past_target_age():
    """Test projection with years=0 (no time left; should return pot unchanged).

    When years <= 0, projected_pot = pot + 0 growth = pot.
    """
    [s2, _, _] = project(Decimal("100"), Decimal("50"), 0, Decimal("200"))
    assert s2.projected_pot == Decimal("100.00") and s2.on_track is False
