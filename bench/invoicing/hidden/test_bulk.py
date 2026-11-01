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


def test_quantity_must_be_positive() -> None:
    for bad in (0, -3):
        with pytest.raises(InvoiceError):
            LineItem("Widget", Decimal("1.00"), bad)


def test_bulk_price() -> None:
    from invoicing.calc import bulk_price
    assert bulk_price(Decimal("2.00"), 99) == Decimal("198.00")
    assert bulk_price(Decimal("2.00"), 100) == Decimal("180.00")
    assert bulk_price(Decimal("0.333"), 150) == Decimal("44.96")
