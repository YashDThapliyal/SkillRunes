# SKILL.md — Invoicing Codebase (bench/invoicing)

## Project Context

- The code lives in `invoicing/calc.py`. It holds pure functions that work on an `Invoice` dataclass.
- `Invoice` fields seen so far: `number`, `customer`, `issued_on` (a `date`), `due_days` (an int), `notes`, `items`, `payments`. `invoice.subtotal()` is a method.
- `Payment(amount, received_on)` is the payment record type.
- Existing helpers in `calc.py`. Reuse them and do not reimplement them:
  - `apply_percentage(amount, percent)` does the percentage math, rounds to cents, and already rejects negative percents.
  - `to_money(value)` rounds a value to cents.
  - `balance_due(invoice)` returns the amount still owed.
  - `add_item` is the pattern for immutable updates: it uses `dataclasses.replace` to return a new `Invoice`.
- Errors live in `errors.py`:
  - `ValidationError` is for bad input.
  - `PaymentError` exists but was unused before. Use it when a payment can't be applied, such as an overpayment.
- `CHANGELOG.md` has a `## Unreleased` section at the top, followed by released sections such as `1.2.0`. The 1.2.0 section contains the `apply_percentage` entry.
- **Project rule:** every change needs a bullet under `## Unreleased`. A code reviewer enforces this and sends back any change without one.

## Required Checklist Before Saying "Done"

Every one of the 15 recorded sessions got "changes requested" on its first submission for the same reason: no CHANGELOG entry. None of them wrote or ran a test. Do these steps in the first submission:

1. **Read `CHANGELOG.md` before you edit any code.** Add a bullet under `## Unreleased` in the same change. Copy the style of the existing entries: past tense, function name in backticks, one line. Examples:
   - `- Added \`add_tax\` helper.`
   - `- Added \`merge_invoices\` to combine two invoices for the same customer.`
2. **Look for other contribution rules** in CONTRIBUTING.md, PR templates and the existing `tests/` layout, and follow them too.
3. **Write tests for each new function and run them.** The user's global rules require TDD and 80% coverage. Look at the existing tests and match their style; if there are none, use pytest. At minimum, cover:
   - a normal case
   - each validation or error path, checking the exception type
   - inclusive boundaries and the values just outside them
4. **Run the whole test suite**, not only the new tests.
5. **Report what you verified and what you didn't.** It's honest to say "not run or tested", but every session that said it still left the work unverified. Say it only when running the code is truly impossible.
6. **When you address review feedback, check for other gaps in the same pass,** such as missing tests or docs. Don't fix only the item the reviewer named. Treat any process feedback as a rule for the whole repo from then on.

## Implementation Patterns That Worked

- **Build on the existing helpers.** For example:
  - `add_tax`: `apply_percentage(invoice.subtotal(), rate)`
  - `discounted_subtotal`: `to_money(subtotal - apply_percentage(subtotal, percent))`
  - `is_overdue`: uses `balance_due(invoice)` instead of computing the balance again
- **Add a domain-specific check even when a lower-level helper already validates.** For example, `add_tax` raises `ValidationError("tax rate cannot be negative")` itself rather than relying on `apply_percentage`'s generic negative-percent error, so the message names the tax rate.
- **Never mutate inputs.** Return a new `Invoice` with `dataclasses.replace`, as `record_payment` and `merge_invoices` do.
- **Round money before validating it.** For example, `record_payment` calls `to_money(amount)` first, so 0.004 becomes 0.00 and is rejected as non-positive, and the checks use the same value that gets stored. Mention this behavior in your summary.
- **Choosing error types in `record_payment`:**
  - Use `ValidationError` for amounts ≤ 0 and `PaymentError` for amounts above `balance_due`.
  - An amount exactly equal to the balance is allowed.
  - Sessions disagreed on this choice, so state it and offer the alternative.
- **Put both conflicting values in the error message,** for example both customer names when `merge_invoices` rejects different customers.
- **Date helpers:**
  - `due_date` returns `issued_on + timedelta(days=due_days)`.
  - `is_overdue` returns `today > due_date(invoice) and balance_due(invoice) > 0`. The `>` is strict, so an invoice is not overdue on its due date, and a fully paid or overpaid invoice is never overdue.
  - Add `from datetime import date, timedelta` to the imports.

## Edge Cases to Handle and Test

| Function | Cases |
|---|---|
| `discounted_subtotal` | 0 and 100 are valid (100 returns 0.00); −1 and 101 are rejected; **Decimal NaN** (comparisons with NaN either raise `InvalidOperation` or pass silently, so reject it explicitly, e.g. `percent.is_nan()`); infinity |
| `add_tax` | normal rate, 0 rate, negative rejected, cents rounding |
| `record_payment` | 0, negative, sub-cent (0.004), exactly the balance, over the balance; the original invoice is unchanged |
| `due_date` / `is_overdue` | on the due date (False), the day after (True), zero balance (False) |
| `merge_invoices` | different customers raise an error; a's items come before b's, and a's payments before b's; a's header is kept; neither input is mutated |

## Ambiguities: Pick One, State It, Offer the Alternative

- **`merge_invoices` notes:** a's header fields (number, customer, issued_on, due_days, notes) are kept, so b's notes are dropped. Say so, and offer to concatenate the notes instead.
- **`merge_invoices` customer match:** the check uses exact string equality, so `'Acme'` and `'acme '` count as different customers. Point this out.
- **Name vs. behavior:** `add_tax` only returns the tax amount and does not change the invoice. Follow the spec and point out that the name suggests otherwise.

## What Changed

This is the first version. It was built from 15 benchmark sessions that added `add_tax`, `discounted_subtotal`, `due_date`/`is_overdue`, `record_payment` and `merge_invoices` to `invoicing/calc.py`. It records the rule that every change needs a CHANGELOG `## Unreleased` entry, which every first submission missed, and requires writing and running tests, which no session did. It also lists the existing helpers and error types to reuse, the edge cases each function needs tested (including Decimal NaN), and how to handle ambiguous specs.