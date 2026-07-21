# Task 3 Report: PDF preview before sending an invoice

## Status
DONE

## Commit
f40c5c742cfbc4d57890a4aaf93418fbe237136d — "Add staff PDF preview endpoint for invoices" (branch `fix/final-evaluation`)

## What changed and why

Staff previously could only download an invoice PDF (forced `attachment` disposition), with no way
to preview it in-browser before emailing a draft. Added a dedicated preview endpoint that reuses the
exact same access rules and PDF generation as the download endpoint, differing only in the response
disposition (`inline` vs `attachment`), matching the codebase's existing pattern of separate
single-purpose views (download pdf / download excel) rather than an overloaded query-param branch.

### Files touched

- `invoicing/views.py` — new `invoice_preview_pdf` view, placed directly after `invoice_download_pdf`.
  Same decorators (`@login_required`, `@role_required(SUPERADMIN, ADMIN, FINANCE)`), same object
  lookup (`get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)`), same
  `generate_invoice_pdf` call, and logs an audit event (`invoice.pdf.previewed`) mirroring the
  existing `invoice.pdf.downloaded` event. Sets
  `Content-Disposition: inline; filename="<invoice_number>.pdf"`.
- `invoicing/urls.py` — new route `<int:pk>/preview/pdf/` named `invoice-preview-pdf`, placed next to
  `invoice-download-pdf`.
- `templates/invoicing/invoice_detail.html` — added a "Preview PDF" button (`btn-outline-info`) in
  the header action group, before "Download PDF", opening in a new tab
  (`target="_blank" rel="noopener"`). Available regardless of invoice status, so it works for drafts.
- `templates/invoicing/invoice_list.html` — added a "Preview" button in the per-row actions column,
  before the existing "PDF" download button, same `target="_blank" rel="noopener"` treatment.
- `invoicing/tests.py` — 4 new tests (in `InvoicingMvpTests`, following existing conventions in the
  file, added after `test_staff_cannot_download_invoice_documents`):
  - `test_finance_can_preview_draft_invoice_pdf_inline` — authorized finance user, invoice in
    `STATUS_DRAFT`, asserts 200, `application/pdf`, `inline` in `Content-Disposition`, filename
    present, and PDF magic bytes.
  - `test_staff_cannot_preview_invoice_pdf` — mirrors the existing
    `test_staff_cannot_download_invoice_documents` pattern; unauthorized role (`staff_u`) gets 403.
  - `test_anonymous_user_cannot_preview_invoice_pdf` — logged-out client gets redirected (302) to the
    login page.
  - `test_invoice_detail_page_shows_preview_pdf_link` — invoice detail page for an authorized user
    contains the preview URL, the "Preview PDF" label, and a `target="_blank"` link.

## Test-first process

1. Wrote the 4 tests above against a not-yet-existing `invoice-preview-pdf` URL/view.
2. Ran them targeted — confirmed all 4 failed with `NoReverseMatch` (red).
3. Implemented the view, URL, and template changes.
4. Re-ran the same 4 targeted tests — all passed (green).
5. Ran the full `invoicing` app test suite as the done gate.

## Test commands + counts

- Targeted (red→green): `.venv/Scripts/python.exe manage.py test invoicing.tests.InvoicingMvpTests.test_finance_can_preview_draft_invoice_pdf_inline invoicing.tests.InvoicingMvpTests.test_staff_cannot_preview_invoice_pdf invoicing.tests.InvoicingMvpTests.test_anonymous_user_cannot_preview_invoice_pdf invoicing.tests.InvoicingMvpTests.test_invoice_detail_page_shows_preview_pdf_link --settings=config.test_settings`
  → 4/4 passed.
- Full done-gate: `.venv/Scripts/python.exe manage.py test invoicing --settings=config.test_settings`
  → **168 tests, OK** (all green, including the 4 new ones).

## Concerns

None significant. Notes:
- The preview endpoint re-generates the PDF on every request (same as the download endpoint already
  did) — no new performance concern introduced, just consistent with existing behavior.
- Reused the same role set (`SUPERADMIN, ADMIN, FINANCE`) as download/send-email views, per the
  brief's instruction to mirror existing access rules.
- Did not add a preview option to the customer-facing views (`customer_invoice_detail` /
  `customer_invoice_download_pdf`) — brief scoped this to the staff invoice detail/list pages only.
