# Project Skill

## Public API conventions

- Whenever you add a public function to invoicing/ (e.g. calc.py), do all four of these in the same change, before the first hand-off: (1) import it in invoicing/__init__.py and add it to __all__; (2) start every error message with the function name and a colon, e.g. 'add_tax: rate_percent cannot be negative'; (3) add a .changeset/<slug>.md release note; (4) add tests. Reviewers rejected every first submission that skipped these. <!-- R1 -->
- The .changeset/ directory may not exist yet. If so, create it. Get the package name from pyproject.toml. Write frontmatter in the form '"invoicing": minor', then tell the user the format was assumed. <!-- R2 -->

## Testing

- Put tests for new calc functions in tests/test_calc.py, and create the file if it doesn't exist. Cover the normal case, exact boundaries (inclusive limits, due date vs. the day after, payment equal to the balance), invalid input with the 'func_name:' message prefix asserted, and that the input invoice is unchanged. Run pytest and ruff check before you report the work as done. <!-- R3 -->

## Codebase helpers

- Reuse the existing helpers in invoicing: apply_percentage and to_money for percentages and cent rounding, balance_due for the amount still owed, and dataclasses.replace for immutable updates (as add_item does). Use the exceptions already in errors.py (ValidationError, PaymentError) instead of defining new ones. Decimal('NaN') comparisons raise InvalidOperation, not ValidationError. <!-- R4 -->

## Environment

- This is macOS with zsh. A glob with no matches (e.g. .changeset/*.md) fails the whole command, so check that the directory exists first or use find. BSD sed -i fails, so edit files with the Edit tool. <!-- R5 -->
