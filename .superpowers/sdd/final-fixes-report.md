# Final Evaluation Fixes Report

Branch: `fix/final-evaluation`

## 1. Invoice number generation robustness

`invoicing/services.py` — `generate_invoice_number()` previously used
`order_by("-invoice_number").first()`, which sorts the `invoice_number`
string lexicographically. Once a year reaches 10,000 invoices,
`"INV-2026-10000"` sorts below `"INV-2026-9999"`, so the "last" invoice
found is the wrong one and the function can regenerate an already-used
number, causing an `IntegrityError` that aborts the whole import.

Fix: instead of relying on string ordering, the function now fetches all
`invoice_number` values for the current year's prefix, parses the numeric
suffix from each (skipping any that fail to parse), and takes `max + 1`.
The existing `:04d` zero-padded format is preserved (numbers below 10000
are still zero-padded to 4 digits; numbers at/above 10000 grow naturally
since `:04d` only enforces a *minimum* width). The empty-case behavior
(`INV-<year>-0001` when there are no existing invoices for the year) is
unchanged.

Regression test added: `InvoicingMvpTests.test_generate_invoice_number_rolls_over_past_four_digits_without_duplicates`
in `invoicing/tests.py`. It seeds an invoice numbered `INV-<year>-9999`,
asserts the next generated number is `INV-<year>-10000`, then seeds that
invoice too and asserts the next number is `INV-<year>-10001` (proving no
duplicate is produced across the 4-digit boundary).

## 2. Renamed misleading test

`invoicing/tests.py` — `test_excel_duplicate_rows_within_same_upload_are_rejected`
actually asserted that two identical rows within the same upload are BOTH
imported as separate invoices (count == 2), not rejected. Renamed to
`test_excel_identical_rows_within_same_upload_each_create_an_invoice` to
match actual behavior. Assertions were left unchanged.

## 3. Removed unused imports

`core/views.py:1` — `from decimal import Decimal, ROUND_HALF_UP` was left
over from the removed CEO context builder and was unused. Verified with a
grep across the file that neither `Decimal` nor `ROUND_HALF_UP` appears
anywhere else in `core/views.py` before removing the import line entirely.

## Commands run (foreground)

```
.venv/Scripts/python.exe manage.py test invoicing.tests.InvoicingMvpTests invoicing.tests.InvoiceFileUploadTests --settings=config.test_settings
# Ran 112 tests in 505.187s - OK

.venv/Scripts/python.exe manage.py test core --settings=config.test_settings
# Ran 23 tests in 19.838s - OK
```

## Files changed

- `invoicing/services.py`
- `invoicing/tests.py`
- `core/views.py`

(This report file lives under `.superpowers/` and is intentionally not committed, per instructions.)
