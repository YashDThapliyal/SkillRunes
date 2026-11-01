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


def test_split_amounts_sums_exactly() -> None:
    from invoicing.calc import split_amounts
    parts = split_amounts(Decimal("100.00"), 3)
    assert parts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(parts) == Decimal("100.00")


def test_split_single_and_invalid() -> None:
    from invoicing.calc import split_amounts
    assert split_amounts(Decimal("0.05"), 1) == [Decimal("0.05")]
    with pytest.raises(InvoiceError):
        split_amounts(Decimal("1.00"), 0)
