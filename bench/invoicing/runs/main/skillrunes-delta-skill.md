# Project Skill

## Process conventions

- Every change must add a bullet under '## Unreleased' in CHANGELOG.md in the same change, worded and formatted like the existing entries (e.g. the 1.2.0 entries). Name the new function and its validation rules. The reviewer rejects any submission that lacks this bullet. <!-- R1 -->

## invoicing/calc.py conventions

- In invoicing/calc.py, build on the existing helpers: apply_percentage for percent math (it already rejects negative percents), to_money for rounding to cents, balance_due(invoice) for the outstanding balance, ValidationError for bad input, and PaymentError (errors.py) when a payment can't be applied. Return new Invoice objects with dataclasses.replace, following the add_item pattern. Never modify an invoice in place. <!-- R2 -->
- For money inputs, round to cents with to_money before validating, so values below one cent become 0.00 and are rejected. When range-checking Decimal inputs, reject NaN explicitly with is_nan(), because NaN fails every comparison and slips past checks like percent < 0 or percent > 100. <!-- R3 -->
