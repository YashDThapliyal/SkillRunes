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


def test_merge_same_customer() -> None:
    from invoicing.calc import merge_invoices
    a = inv(("A", "1.00", 1), number="INV-1")
    b = inv(("B", "2.00", 2), number="INV-2", payments=(Payment(Decimal("1.00"), date(2026, 1, 2)),))
    merged = merge_invoices(a, b)
    assert merged.number == "INV-1"
    assert merged.subtotal() == Decimal("5.00")
    assert merged.paid() == Decimal("1.00")


def test_merge_rejects_different_customers() -> None:
    from invoicing.calc import merge_invoices
    with pytest.raises(InvoiceError):
        merge_invoices(inv(("A", "1.00", 1)), inv(("B", "1.00", 1), customer="Globex"))
