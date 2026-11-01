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


def test_discounted_subtotal() -> None:
    from invoicing.calc import discounted_subtotal
    assert discounted_subtotal(inv(("A", "19.99", 3)), Decimal("15")) == Decimal("50.97")


def test_discount_bounds() -> None:
    from invoicing.calc import discounted_subtotal
    assert discounted_subtotal(inv(("A", "5.00", 1)), Decimal("100")) == Decimal("0.00")
    for bad in (Decimal("-1"), Decimal("100.01")):
        with pytest.raises(InvoiceError):
            discounted_subtotal(inv(("A", "5.00", 1)), bad)
