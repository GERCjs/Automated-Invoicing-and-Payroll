# Task 4 Report: Invoice importer — one valid row = one invoice

## Summary
Removed the customer+email+booked-month grouping from the invoice CSV/Excel
importer. Each valid parsed row now produces exactly one `Invoice` with a
single `InvoiceItem`, per the supervisor's rule. Invalid-row handling
(`ImportJob`/`ImportRowError`) and row-level duplicate detection were
already row-scoped and did not need to change.

## Files changed
- `invoicing/services.py`
  - `_build_invoice_parse_result`: replaced the `preview_groups`
    (customer+email+period grouping via `defaultdict`) with `preview_invoices`,
    a flat list with one entry per valid row (`customer_name`, `period`,
    `order_id`, `amount_total`). Removed the now-unused `collections.defaultdict`
    import.
  - `import_invoice_rows_from_preview`: removed the `grouped_valid_rows`
    defaultdict and the per-group loop. Now iterates `importable_valid_rows`
    directly, creating one `Invoice` + one `InvoiceItem` per row.
    - `issue_date` per invoice is now the row's own `booked_at` date (falls
      back to today if missing) instead of `min()` over a group's booked
      dates.
    - Customer resolution/creation logic is unchanged in behavior: rows for
      the same customer email still hit `customers_cache` and reuse/update
      the same `Customer` record — they just each get their own `Invoice`
      now instead of being merged.
    - `generate_invoice_number()` is called once per `Invoice.objects.create()`
      inside the same `transaction.atomic()` block, sequentially (not
      bulk_create), so each call sees the previously created invoice in the
      same transaction/connection — numbering still allocates without
      collisions across many invoices in one import (verified by a new test
      asserting 5 distinct invoice numbers from a 5-row import).
  - Row-level duplicate detection (`_invoice_row_duplicate_key`,
    `_existing_imported_invoice_row_keys`) was already keyed on
    order_id/customer/service/booked_date/amount, not the group key, so no
    changes were needed there. Note: duplicate rows *within the same upload
    batch* were never checked against each other (only against previously
    imported data) — that pre-existing behavior is unchanged and is now
    covered explicitly by a renamed test.

- `invoicing/views.py`
  - `invoice_csv_upload`: renamed `preview_groups` → `preview_invoices` in
    the session payload, and `group_preview_rows` → `invoice_preview_rows`
    in the render context, to match the new one-row-one-invoice preview
    shape.

- `templates/invoicing/invoice_csv_upload_preview.html`
  - "Invoice Upload Preview" table badge now reads "N invoice(s)" instead of
    "N group(s)".
  - Table columns changed from Merchant/Customer, Period, Rows, Amount to
    Merchant/Customer, Order ID, Period, Amount (the "Rows" count column is
    gone since every row is always 1 invoice now; Order ID added so staff
    can identify which row maps to which future invoice).
  - Empty-state text changed from "No valid groups to import." to "No valid
    rows to import."

- `invoicing/tests.py`
  - `InvoicingMvpTests.test_finance_can_preview_and_confirm_invoice_csv_import`:
    updated to expect 2 invoices (one per row) instead of 1 invoice with 2
    items; sums `total_amount` across both invoices to assert 200.00.
  - `InvoiceFileUploadTests.test_confirmation_saves_valid_excel_rows_only`:
    updated to expect 2 invoices (1 item each) instead of 1 invoice with 2
    items; sums totals to assert 200.00.
  - `InvoiceFileUploadTests.test_excel_duplicate_rows_keep_existing_grouping_behavior`
    renamed to `test_excel_duplicate_rows_within_same_upload_are_rejected`
    (misleading old name referenced "grouping") and updated to expect 2
    separate invoices (each with 1 item, 120.00) totalling 240.00 instead of
    1 invoice with 2 items.
  - Added new test
    `InvoiceFileUploadTests.test_n_valid_rows_same_customer_and_month_create_n_invoices`:
    imports 5 valid rows for the same customer/email/booked-month (distinct
    OrderID/service/amount per row) and asserts: preview shows 5 valid rows,
    5 invoices are created, invoice numbers are all distinct (no collision),
    each invoice has exactly 1 item whose `line_total` and the invoice's
    `total_amount` match the row's amount, and `ImportJob.saved_rows == 5`.

## Behavioral notes
- Preview counts: previously the preview table showed one row per
  customer+email+month group with an aggregate row count and amount total.
  Now it shows one row per valid spreadsheet row (i.e., one row per invoice
  that will be created), with that row's own order ID, period, and amount.
  The top "Preview Summary" card (`Total Rows` / `Valid Rows` / `Invalid
  Rows`) was already row-based and unaffected.
- Duplicate detection: unchanged in mechanism (already row-level, keyed on
  order_id + customer + service_name + booked_date + amount against
  previously imported `InvoiceSourceRow`/`InvoiceItem` data). Behaviorally
  visible change: two rows in the same batch that are identical to each
  other (not previously imported) are no longer merged into one invoice —
  they now become two separate invoices, matching the "one row = one
  invoice" rule.
- Invoice numbering: confirmed no collisions across many invoices in a
  single import because invoices are still created one at a time inside a
  single `transaction.atomic()` block, so `generate_invoice_number()`
  observes each prior insert.

## Test commands and results
- Targeted: `.venv/Scripts/python.exe manage.py test invoicing.tests.InvoiceFileUploadTests --settings=config.test_settings` → 32 tests, OK.
- Done gate (foreground): `.venv/Scripts/python.exe manage.py test invoicing imports --settings=config.test_settings` → **171 tests, OK** (~539s).

## Concerns
- None outstanding. The change is scoped to `invoicing/services.py`,
  `invoicing/views.py`, the upload preview template, and importer tests.
  No other callers of `preview_groups`/`group_period` grouping logic were
  found in the codebase (grep confirmed `group_period` per-row field is
  still used and unaffected; only the removed grouping dict/key was
  renamed/removed).
