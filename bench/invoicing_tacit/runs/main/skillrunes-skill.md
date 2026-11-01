## Project Context

- Python package `invoicing/`. Domain logic lives in `invoicing/calc.py`, models in `invoicing/models.py` (Invoice, Payment), exceptions in `invoicing/errors.py` (`ValidationError`, `PaymentError`). Tests live under `tests/`, for example `tests/test_models.py`, and any `tests/test_calc.py` you create.
- Existing helpers in `calc.py` that you should reuse rather than reimplement:
  - `apply_percentage(amount, percent)`: already rejects negative percentages with `ValidationError` and already rounds through `to_money`.
  - `to_money(value)`: rounds to cents.
  - `balance_due(invoice)`: subtotal minus payments. Treat overpaid as no balance.
  - `invoice.subtotal()`
  - `add_item`: shows the immutable update pattern, `dataclasses.replace(...)` with a new list.
- `errors.py` has `PaymentError` defined, though no code uses it yet. Use it for payment-specific failures such as overpayment before you invent a new exception class.
- Tooling: `pytest` for tests, `ruff check` for lint. The environment is macOS with the zsh shell.

## Definition of Done for Any New Public Function (repo review checklist)

Every first submission in past sessions came back from review because it skipped these steps. Do all four in the **first** change, without waiting to be asked:

1. **Export it.** Import the new function in `invoicing/__init__.py` and add its name to `__all__`, following the existing style. Don't reformat `__all__` beyond what the addition needs. Export only the new function or functions. If other `calc.py` functions are also unexported, mention it rather than fixing it unasked.
2. **Prefix error messages with the function name.** Every message a new `raise` produces starts with `function_name: `, e.g. `add_tax: rate_percent cannot be negative`, `discounted_subtotal: percent must be between 0 and 100`, `merge_invoices: cannot merge invoices for different customers`, `record_payment: ...`. Leave messages in existing functions (e.g. `apply_percentage`) alone, and mention any inconsistency you notice.
3. **Add a changeset.** Create `.changeset/<kebab-description>.md`, e.g. `.changeset/add-tax.md`, with a short release note. The `.changeset/` directory has never existed in this repo, so create it. With no config to copy, past sessions used either a guessed frontmatter (`"invoicing": minor`) or none at all. Whichever you choose, **tell the user the package name, bump level and format are assumptions** and ask them to check these against their release tooling.
4. **Write tests and run them.** Add at least one happy-path test and one invalid-input test per new public function. If no test file exists for `calc.py`, create `tests/test_calc.py`; a missing file is no excuse to skip tests. Run `pytest` and `ruff check` before you report, and state the pass count (the baseline suite has about 5 tests, and 7–10 after additions).

Run this checklist before the first hand-off. "Not run or tested" said honestly is better than implying the code was verified, but shipping verified code is better than both.

## Implementation Patterns

- **Reuse helpers so rounding and validation behave the same everywhere.** `add_tax` = `apply_percentage(invoice.subtotal(), rate_percent)`. `discounted_subtotal` = `to_money(subtotal - apply_percentage(subtotal, percent))`. `is_overdue` uses `balance_due`.
- Add an explicit validation check even when a helper already validates, if that lets the error name the right argument with the function prefix (e.g. `add_tax: rate_percent ...`).
- **Never mutate inputs.** Return a new invoice via `replace`, as `add_item` does, and add a test that the original is unchanged (`record_payment`, `merge_invoices`).
- **Money validation:** normalize with `to_money` *before* validating, so sub-cent values like `0.004` become `0.00` and get rejected as non-positive. Mention this behaviour to the user.
- **Decide boundaries explicitly, then test both sides:**
  - Inclusive ranges: for a 0–100 percent, test `0` and `100` (100% discount → `0.00`), plus `-0.01` and `100.01`.
  - Limits: a payment exactly equal to the balance due is allowed; overpayment raises.
  - Dates: `is_overdue` is `today > due_date and balance_due > 0`. It is false on the due date and true the day after if a balance is owed. Fully paid or overpaid invoices are never overdue. `due_date = issued_on + timedelta(days=due_days)`.
  - Zero inputs: a zero tax rate gives `0.00`.
- **Special numeric values:** `Decimal('NaN')` comparisons can raise `InvalidOperation` instead of `ValidationError`. Reject NaN/inf explicitly and test it, or flag it as unverified.
- **State the choices the spec left open** in the first report and offer the alternative. For `merge_invoices`: `a`'s header fields (number, customer, issue date, due days, notes) are kept, `b`'s notes are dropped, items and payments are ordered `a` then `b`, and the customer match is exact and case-sensitive.
- Assert the error-message prefix in tests, e.g. `pytest.raises(ValidationError, match="^add_tax:")`.

## Shell / Tooling Pitfalls

- **zsh unmatched globs fail the whole command.** `ls .changeset/*.md` errors with "no matches found" (exit 1) when the directory is missing, and that error took down whole combined discovery commands in about 6 of 15 sessions. Check existence first, e.g. `[ -d .changeset ] && ls .changeset`, or run each probe as a separate command.
- **Don't use `sed -i` on macOS.** BSD sed's in-place syntax differs from GNU and silently failed twice. Use the Edit tool for file changes.

## Reporting

- Answer review feedback item by item, saying how each point was addressed.
- Say what was and wasn't verified: tests run with the count, lint result, and any edge cases left unchecked.
- List every guessed value (changeset package name, bump level, frontmatter) for the user to confirm.
- Keep review fixes minimal. Call out any extra change you made, such as a reformat.

## What Changed

This is the first version, built from 15 bench sessions that added functions to `invoicing/calc.py` (add_tax, discounted_subtotal, due_date/is_overdue, merge_invoices, record_payment). It records the four review requirements every first submission missed: export via `__init__`/`__all__`, the `function_name:` error prefix, a `.changeset/` entry, and tests that were actually run. It also covers which helpers to reuse, boundary-testing rules, and the zsh-glob and BSD-sed failures behind every tool error.