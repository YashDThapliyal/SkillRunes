"""Pure functions that derive values or new invoices from an Invoice."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from invoicing.errors import ValidationError
from invoicing.log import get_logger
from invoicing.models import Invoice, LineItem
from invoicing.money import to_money

logger = get_logger("calc")


def add_item(invoice: Invoice, item: LineItem) -> Invoice:
    """Return a copy of invoice with item appended."""
    logger.debug("adding item %s to %s", item.description, invoice.number)
    return replace(invoice, items=(*invoice.items, item))


def balance_due(invoice: Invoice) -> Decimal:
    """Amount still owed: subtotal minus payments, never below zero."""
    remaining = invoice.subtotal() - invoice.paid()
    return max(to_money(remaining), Decimal("0.00"))


def apply_percentage(amount: Decimal, percent: Decimal) -> Decimal:
    """Return percent% of amount, rounded to cents."""
    if percent < 0:
        raise ValidationError("percent cannot be negative")
    return to_money(amount * percent / Decimal("100"))
