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


def test_late_fee_accrues_daily() -> None:
    from invoicing.calc import late_fee
    invoice = inv(("A", "100.00", 1), due_days=10)
    assert late_fee(invoice, date(2026, 1, 14), Decimal("0.5")) == Decimal("1.50")


def test_no_fee_when_not_overdue_or_paid() -> None:
    from invoicing.calc import late_fee
    invoice = inv(("A", "100.00", 1), due_days=10)
    assert late_fee(invoice, date(2026, 1, 11), Decimal("0.5")) == Decimal("0.00")
    paid = inv(("A", "100.00", 1), due_days=10, payments=(Payment(Decimal("100.00"), date(2026, 1, 2)),))
    assert late_fee(paid, date(2026, 3, 1), Decimal("0.5")) == Decimal("0.00")


def test_late_fee_rejects_negative_rate() -> None:
    from invoicing.calc import late_fee
    with pytest.raises(InvoiceError):
        late_fee(inv(("A", "1.00", 1)), date(2026, 3, 1), Decimal("-0.1"))
