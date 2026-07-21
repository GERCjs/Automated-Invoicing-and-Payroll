# Task 3: PDF preview before sending an invoice

## Goal (supervisor requirement)
Staff must be able to clearly preview an invoice PDF before emailing it, while it is still a draft. Currently PDF access is download-only.

## Situation
- PDF generation: `generate_invoice_pdf` (invoicing/exports.py:393).
- Staff download endpoint: `invoice_download_pdf` (invoicing/views.py:1482), URL name `invoice-download-pdf` (invoicing/urls.py:21) — responds with `Content-Disposition: attachment` (forces download).
- Email send flow: `invoice_send_email` (invoicing/views.py:1521), batch at :1540; drafts are allowed in batch send.
- Staff invoice detail view: `invoice_detail` (invoicing/views.py, template under templates/invoicing/) — this is where the preview button belongs. There is also an invoice list with send actions.

## Required work
1. Add a staff-only "preview PDF" endpoint that serves the same generated PDF with `Content-Disposition: inline; filename=...` so it opens in the browser tab instead of downloading. Reuse the existing permission/role checks and object lookup used by `invoice_download_pdf` (same access rules). Simplest acceptable design: a new view + URL (e.g. `invoice-preview-pdf`), or extend the existing download view with a `?preview=1`/`disposition=inline` parameter — pick whichever matches the codebase style better and keep it consistent.
2. Add a clearly labeled "Preview PDF" link/button on the staff invoice detail page (and, if the invoice list has per-row send/download actions, add preview there too), opening in a new tab (`target="_blank" rel="noopener"`). It must be available for draft invoices (available for all statuses is fine).
3. Tests: (a) preview endpoint returns 200 with `application/pdf` content type and inline disposition for an authorized staff user on a DRAFT invoice; (b) unauthorized/anonymous user cannot access it (mirror the access assertions of existing download tests); (c) the invoice detail page contains the preview link. Follow existing test conventions in invoicing/tests.py.

## Done criteria
- `.venv/Scripts/python.exe manage.py test invoicing --settings=config.test_settings` fully green.
- Commit on branch fix/final-evaluation.
