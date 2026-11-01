from datetime import date
from decimal import Decimal

import pytest

from invoicing import Invoice, LineItem, ValidationError
from invoicing.calc import add_item, apply_percentage, balance_due


def make_invoice() -> Invoice:
    return Invoice(number="INV-1", customer="Acme", issued_on=date(2026, 1, 1))


def test_subtotal_sums_items() -> None:
    inv = add_item(make_invoice(), LineItem("Widget", Decimal("2.50"), 4))
    assert inv.subtotal() == Decimal("10.00")


def test_balance_due_never_negative() -> None:
    assert balance_due(make_invoice()) == Decimal("0.00")


def test_apply_percentage_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        apply_percentage(Decimal("10"), Decimal("-1"))


def test_line_item_requires_description() -> None:
    with pytest.raises(ValidationError):
        LineItem(" ", Decimal("1"))
