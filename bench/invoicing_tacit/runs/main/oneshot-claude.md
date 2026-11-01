# CLAUDE.md

Small Python invoicing library (`invoicing/`) used by the billing service. No dependencies beyond the stdlib; tests use pytest, linting uses ruff (a `.ruff_cache/` exists; no config file is checked in).

## Commands
- Test all: `python -m pytest -q`
- Single test: `python -m pytest -q tests/test_models.py::test_subtotal_sums_items`
- Lint: `ruff check .` (and `ruff format .` if formatting)

## Layout
- `models.py` – frozen dataclasses: `Invoice`, `LineItem`, `Payment`
- `calc.py` – pure functions that derive values or new invoices from an `Invoice`
- `money.py` – `to_money` (half-up quantise to cents), `format_money`
- `errors.py` – exception hierarchy
- `log.py` – `get_logger`
- `__init__.py` – public API; update `__all__` when exporting new names

## Conventions
- **Immutability:** models are `@dataclass(frozen=True)` with tuple collections. Never mutate; add behaviour as functions in `calc.py` that return a new object via `dataclasses.replace`.
- **Money:** always `Decimal`, never `float`. Pass every result through `to_money()` so it is quantised to cents (ROUND_HALF_UP).
- **Errors:** raise subclasses of `InvoiceError` (`ValidationError` for bad input, `PaymentError` for payments). Don't raise bare `ValueError`/`Exception` for domain failures. Validate in `__post_init__` or at function entry.
- **Logging:** use `get_logger("<module>")` from `invoicing.log` (namespaced `invoicing.<name>`); never `print` or call `logging.getLogger` directly. Module-level `logger = get_logger("...")`.
- **Style:** `from __future__ import annotations` in modules, full type hints (including `-> None`), a docstring on every module and public function, absolute imports (`from invoicing.x import ...`).
- **Tests:** in `tests/`, plain pytest functions with type-annotated `-> None`, mirroring the style of `tests/test_models.py`. Add tests for new behaviour.

## Do not touch
- `tests/legacy/` – frozen v1 billing export snapshots ("Do not edit"). If they fail, fix the code, not the tests.
