"""Deterministic retirement projection module for ORSO."""

from dataclasses import dataclass
from decimal import Decimal

RATES = (Decimal("0.02"), Decimal("0.05"), Decimal("0.08"))


@dataclass(frozen=True)
class Scenario:
    """Retirement projection scenario at a specific growth rate.

    Attributes:
        rate: Annual interest rate as a Decimal (e.g., Decimal("0.05") for 5%).
        projected_pot: Total projected value at retirement, quantized to 2 decimal places.
        on_track: Whether the projected_pot meets or exceeds target_pot.
                  None if target_pot was not provided.
        gap: Difference between projected_pot and target_pot (projected_pot - target_pot).
             None if target_pot was not provided.
    """
    rate: Decimal
    projected_pot: Decimal
    on_track: bool | None
    gap: Decimal | None


def project(
    pot: Decimal,
    monthly_contribution: Decimal,
    years: int,
    target_pot: Decimal | None,
) -> list[Scenario]:
    """Project retirement portfolio growth with monthly compounding.

    Uses the future value formula with monthly contributions:
    fv = pot * (1+r)^n + monthly_contribution * (((1+r)^n - 1) / r)

    Where:
        r = rate / 12 (monthly interest rate)
        n = years * 12 (number of months)

    Args:
        pot: Initial investment amount.
        monthly_contribution: Monthly contribution amount.
        years: Number of years to project (if <= 0, no growth occurs).
        target_pot: Target retirement amount, or None if not specified.

    Returns:
        A list of three Scenario objects, one for each rate in RATES (0.02, 0.05, 0.08),
        sorted by rate.
    """
    scenarios = []

    for rate in RATES:
        # When years <= 0, no time for growth; projected_pot = pot
        if years <= 0:
            fv = pot
        else:
            # Calculate monthly interest rate and number of periods
            r = rate / 12
            n = years * 12

            # Principal growth: pot * (1+r)^n
            fv_pot = pot * ((1 + r) ** n)

            # Contribution annuity: monthly_contribution * (((1+r)^n - 1) / r)
            # When monthly_contribution is 0, skip this term for cleanliness
            if monthly_contribution == 0:
                fv_contrib = Decimal("0")
            else:
                fv_contrib = monthly_contribution * (((1 + r) ** n - 1) / r)

            fv = fv_pot + fv_contrib

        # Quantize to 2 decimal places
        fv = fv.quantize(Decimal("0.01"))

        # Compute on_track and gap only if target_pot is provided
        if target_pot is None:
            on_track = None
            gap = None
        else:
            gap = fv - target_pot
            on_track = fv >= target_pot

        scenarios.append(Scenario(rate=rate, projected_pot=fv, on_track=on_track, gap=gap))

    return scenarios
