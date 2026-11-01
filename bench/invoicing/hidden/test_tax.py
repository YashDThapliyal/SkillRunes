from datetime import date
from decimal import Decimal

import pytest

from invoicing import Invoice, InvoiceError, LineItem, Payment


def inv(*items: tuple[str, str, int], payments: tuple[Payment, ...] = (), customer: str = "Acme", number: str = "INV-9", due_days: int = 30) -> Invoice:
    return Invoice(
        number=number,
        customer=customer,
        issued_on=date(2026, 1, 1),
        items=tuple(LineItem(d, Decimal(p), q) for d, p, q in items),
        payments=payments,
        due_days=due_days,
    )


def test_tax_rounds_half_up() -> None:
    from invoicing.calc import add_tax
    assert add_tax(inv(("A", "10.00", 1)), Decimal("8.25")) == Decimal("0.83")


def test_tax_zero_rate() -> None:
    from invoicing.calc import add_tax
    assert add_tax(inv(("A", "10.00", 1)), Decimal("0")) == Decimal("0.00")


def test_tax_rejects_negative() -> None:
    from invoicing.calc import add_tax
    with pytest.raises(InvoiceError):
        add_tax(inv(("A", "10.00", 1)), Decimal("-1"))
