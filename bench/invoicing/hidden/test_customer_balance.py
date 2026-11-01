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


def test_customer_balance() -> None:
    from invoicing.calc import customer_balance
    invoices = [
        inv(("A", "10.00", 1), customer="Acme"),
        inv(("A", "5.00", 1), customer="Acme", payments=(Payment(Decimal("2.00"), date(2026, 1, 2)),)),
        inv(("A", "99.00", 1), customer="Globex"),
    ]
    assert customer_balance(invoices, "Acme") == Decimal("13.00")
    assert customer_balance(invoices, "Nobody") == Decimal("0.00")
    assert customer_balance([], "Acme") == Decimal("0.00")
