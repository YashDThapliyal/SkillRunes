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


def test_refund_appends_negative_payment() -> None:
    from invoicing.calc import refund
    paid = inv(("A", "50.00", 1), payments=(Payment(Decimal("50.00"), date(2026, 1, 2)),))
    refunded = refund(paid, Decimal("20.00"), date(2026, 1, 5))
    assert refunded.paid() == Decimal("30.00")
    assert refunded.payments[-1] == Payment(Decimal("-20.00"), date(2026, 1, 5))
    assert len(paid.payments) == 1


def test_refund_validation() -> None:
    from invoicing.calc import refund
    paid = inv(("A", "50.00", 1), payments=(Payment(Decimal("50.00"), date(2026, 1, 2)),))
    for bad in (Decimal("0"), Decimal("50.01")):
        with pytest.raises(InvoiceError):
            refund(paid, bad, date(2026, 1, 5))
