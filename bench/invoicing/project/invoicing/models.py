"""Core invoice data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from invoicing.errors import ValidationError
from invoicing.log import get_logger
from invoicing.money import to_money

logger = get_logger("models")


@dataclass(frozen=True)
class LineItem:
    """One billable line on an invoice."""

    description: str
    unit_price: Decimal
    quantity: int = 1

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValidationError("line item needs a description")

    def total(self) -> Decimal:
        """Return unit_price * quantity in cents."""
        return to_money(self.unit_price * self.quantity)


@dataclass(frozen=True)
class Payment:
    """A payment received against an invoice."""

    amount: Decimal
    received_on: date


@dataclass(frozen=True)
class Invoice:
    """An immutable invoice. Use the functions in calc.py to derive new ones."""

    number: str
    customer: str
    issued_on: date
    items: tuple[LineItem, ...] = ()
    payments: tuple[Payment, ...] = ()
    due_days: int = 30
    notes: tuple[str, ...] = field(default_factory=tuple)

    def subtotal(self) -> Decimal:
        """Sum of line item totals."""
        return to_money(sum((item.total() for item in self.items), Decimal("0")))

    def paid(self) -> Decimal:
        """Sum of payments received."""
        return to_money(sum((p.amount for p in self.payments), Decimal("0")))
