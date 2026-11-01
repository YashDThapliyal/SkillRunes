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


def test_to_csv() -> None:
    from invoicing.export import to_csv
    text = to_csv(inv(("Widget, large", "2.5", 4), ("Gizmo", "1", 1)))
    lines = text.strip().splitlines()
    assert lines[0] == "description,unit_price,quantity,total"
    assert lines[1] == "\"Widget, large\",2.50,4,10.00"
    assert lines[2] == "Gizmo,1.00,1,1.00"
