"""Logging helpers. All modules log through get_logger()."""

import logging

_ROOT = "invoicing"


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger for an invoicing module."""
    return logging.getLogger(f"{_ROOT}.{name}")
