# CLAUDE.md

Small Python invoicing library (`invoicing/`) used by the billing service. No third-party runtime deps; tests use pytest, linting uses ruff (`.ruff_cache` present).

## Commands
- Test all: `python -m pytest -q`
- Single test: `python -m pytest -q tests/test_models.py::test_subtotal_sums_items`
- Lint: `ruff check .` (and `ruff format .` if formatting)

## Layout
- `models.py` – frozen dataclasses: `Invoice`, `LineItem`, `Payment`
- `calc.py` – pure functions that derive values or new invoices (e.g. `add_item`, `balance_due`, `apply_percentage`)
- `money.py` – `to_money`, `format_money`; `errors.py` – exception hierarchy; `log.py` – `get_logger`
- Public API is re-exported in `invoicing/__init__.py` (`__all__`); update it when adding public names.

## Conventions
- **Immutability:** models are `@dataclass(frozen=True)`; collections are tuples. Never mutate — return new objects via `dataclasses.replace` (see `calc.add_item`).
- **Money:** always `Decimal`, never float. Pass every computed amount through `to_money` (quantizes to cents, ROUND_HALF_UP).
- **Errors:** raise subclasses of `InvoiceError` (`ValidationError`, `PaymentError`), never bare `ValueError`/`Exception`. Validate in `__post_init__` or at function entry.
- **Logging:** use `get_logger("<module>")` from `invoicing.log` (namespaced `invoicing.<name>`); don't call `logging.getLogger` directly.
- Modules start with a docstring, use `from __future__ import annotations`, full type hints, and a one-line docstring on public functions.
- Business logic that derives values belongs in `calc.py` as pure functions; keep `models.py` to data and simple derived totals.

## Tests
- Put new tests in `tests/` (e.g. `tests/test_<area>.py`), plain pytest functions with `-> None` annotations.
- `tests/legacy/` holds frozen v1 snapshot tests — **do not edit**. If they fail, fix the code, not the tests.

## Changelog
Record user-visible changes under `## Unreleased` in `CHANGELOG.md`.
