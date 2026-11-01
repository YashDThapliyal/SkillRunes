"""Money helpers. Amounts are always Decimal, quantised to cents."""

from decimal import ROUND_HALF_UP, Decimal

from invoicing.errors import ValidationError

CENT = Decimal("0.01")


def to_money(value: Decimal | int | str) -> Decimal:
    """Convert value to a Decimal rounded half-up to whole cents."""
    try:
        amount = Decimal(value)
    except Exception as exc:  # decimal.InvalidOperation and friends
        raise ValidationError(f"not a money amount: {value!r}") from exc
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, currency: str = "USD") -> str:
    """Render an amount like 'USD 1,234.50'."""
    return f"{currency} {to_money(amount):,.2f}"
