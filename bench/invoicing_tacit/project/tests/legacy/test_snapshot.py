"""Frozen snapshot tests for the v1 billing export. Do not edit."""

from datetime import date
from decimal import Decimal

from invoicing import Invoice, LineItem


def test_v1_snapshot_subtotal() -> None:
    inv = Invoice("INV-0", "Legacy", date(2020, 1, 1), items=(LineItem("Seat", Decimal("9.99"), 3),))
    assert inv.subtotal() == Decimal("29.97")
