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


def test_credit_note_negates_items() -> None:
    from invoicing.calc import credit_note
    original = inv(("A", "10.00", 2), ("B", "1.50", 1))
    note = credit_note(original, "duplicate billing")
    assert note.number == "INV-9-CN"
    assert note.subtotal() == Decimal("-21.50")
    assert "duplicate billing" in note.notes
    assert note.customer == original.customer


def test_credit_note_requires_reason() -> None:
    from invoicing.calc import credit_note
    with pytest.raises(InvoiceError):
        credit_note(inv(("A", "10.00", 1)), "   ")
