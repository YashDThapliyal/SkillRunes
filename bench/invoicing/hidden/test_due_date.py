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


def test_due_date_and_overdue() -> None:
    from invoicing.calc import due_date, is_overdue
    invoice = inv(("A", "10.00", 1), due_days=14)
    assert due_date(invoice) == date(2026, 1, 15)
    assert not is_overdue(invoice, date(2026, 1, 15))
    assert is_overdue(invoice, date(2026, 1, 16))


def test_paid_invoice_is_never_overdue() -> None:
    from invoicing.calc import is_overdue
    invoice = inv(("A", "10.00", 1), payments=(Payment(Decimal("10.00"), date(2026, 1, 2)),))
    assert not is_overdue(invoice, date(2027, 1, 1))
