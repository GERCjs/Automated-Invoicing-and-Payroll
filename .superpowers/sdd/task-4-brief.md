# Task 4: Invoice importer — one valid spreadsheet row = one invoice

## Goal (supervisor requirement)
The supervisor's rule: one valid spreadsheet row creates exactly one invoice. The current importer groups rows by customer+email+booked-month into a single invoice with multiple line items. No later approval of grouping is recorded anywhere in the repo docs, so the original rule stands: remove the grouping.

## Situation (invoicing/services.py)
- Group key built at parse time: line ~559: `key = f"{row['customer_name']}|{row['email'] or 'no-email'}|{row['group_period']}"` with `group_period` from `_invoice_group_period` (~:392-394, booked date %Y-%m).
- Import execution regroups the same way: `grouped_valid_rows` defaultdict (~:693-697); one `Invoice` per group (~:781-791); one `InvoiceItem` per row within the group (~:793-805); then `recalculate_invoice_totals`.
- Entry points: `parse_invoice_upload` (:666) → `parse_invoice_csv` (:584) / `parse_invoice_excel` (:620); commit via `import_invoice_rows_from_preview` (:675); driven from `invoicing/views.py:375 invoice_csv_upload` (preview/confirm session flow). `InvoiceSourceRow` (invoicing/models.py:164) stores raw rows.

## Required work
1. Change the importer so each VALID parsed row produces its own Invoice with a single InvoiceItem (and its InvoiceSourceRow), rather than being merged into a customer+month invoice. Invalid rows keep the existing error handling (ImportJob / ImportRowError reporting unchanged).
2. Keep per-invoice behavior intact: totals recalculated per invoice, invoice numbering allocation must handle many invoices per import without collisions, customer resolution/creation logic unchanged (multiple rows for the same customer still map to the same Customer — just separate invoices).
3. Preview flow: the upload preview shown to staff should reflect reality (one row → one invoice). Adjust preview grouping/summary counts accordingly.
4. Update existing importer tests that assert grouping behavior (rows merged into one invoice, item counts per invoice) to assert the new one-row-one-invoice behavior — the intent flips, but keep the scenarios (multiple rows same customer/month now → that many invoices). Add a direct test: importing N valid rows for the same customer and month creates N invoices, each with 1 item and correct totals.
5. Do not break the duplicate-detection logic if any exists (e.g. skipping rows already imported) — adapt it to row-level rather than group-level if needed.

## Done criteria
- `.venv/Scripts/python.exe manage.py test invoicing imports --settings=config.test_settings` fully green.
- Commit on branch fix/final-evaluation.
