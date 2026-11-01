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


def test_convert_total() -> None:
    from invoicing.calc import convert_total
    assert convert_total(inv(("A", "10.00", 3)), Decimal("0.9234")) == Decimal("27.70")


def test_convert_rejects_non_positive_rate() -> None:
    from invoicing.calc import convert_total
    for bad in (Decimal("0"), Decimal("-1.2")):
        with pytest.raises(InvoiceError):
            convert_total(inv(("A", "1.00", 1)), bad)
