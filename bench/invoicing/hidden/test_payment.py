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


def test_record_payment_returns_new_invoice() -> None:
    from invoicing.calc import balance_due, record_payment
    original = inv(("A", "10.00", 2))
    updated = record_payment(original, Decimal("5.00"), date(2026, 1, 3))
    assert balance_due(updated) == Decimal("15.00")
    assert original.payments == ()


def test_record_payment_validation() -> None:
    from invoicing.calc import record_payment
    for bad in (Decimal("0"), Decimal("-1"), Decimal("20.01")):
        with pytest.raises(InvoiceError):
            record_payment(inv(("A", "10.00", 2)), bad, date(2026, 1, 3))
