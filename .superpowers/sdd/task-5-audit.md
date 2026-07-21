# Task 5 Audit — Reports (Part A) & Customer Portal (Part B)

Branch: `fix/final-evaluation`
Method: read implementation code + existing tests, mapped requirement → code → test, ran targeted Django test suites in foreground.

Test runs confirmed:
- `manage.py test reports --settings=config.test_settings` → 64/64 passed
- `manage.py test payroll.tests.PayrollReportPlacementAndAccessTests --settings=config.test_settings` → 10/10 passed
- `manage.py test payments --settings=config.test_settings` → 66/66 passed
- `invoicing.tests.InvoicingMvpTests.test_customer_cannot_view_other_customer_invoice` → passed

## Findings table

| # | Severity | Area | File:Line | Description | Suggested fix |
|---|---|---|---|---|---|
| 1 | Important | Part A — Reports / refund totals | `reports/views.py:1082` (impl, correct) vs `reports/tests.py` (missing test) | Refund total logic correctly filters `PaymentRefund.objects.filter(status=PaymentRefund.STATUS_SUCCEEDED)`, matching the convention in `invoicing/exports.py:144`. However, no test in `reports/tests.py` creates mixed-status `PaymentRefund` rows (succeeded/pending/failed) and asserts the report total excludes non-succeeded ones. A future refactor could silently sum all refunds or key off `Invoice.status == REFUNDED` alone and no test would catch the regression. | Add a test to `PaymentStripeReportAccessTests` (or new class) creating one succeeded, one pending, and one failed `PaymentRefund` on different invoices; assert `response.context["refunded_amount"]` equals only the succeeded amount. |
| 2 | Important | Part B — Customer portal / IDOR | `payments/views.py:423-431` (`submit_customer_bank_transfer_notice`, impl looks correct) vs `payments/tests.py` (missing test) | This write endpoint uses the same protective queryset pattern (`Invoice` filtered by `customer__email__iexact=user_email`) verified elsewhere (e.g. `checkout_customer_invoice`), so the code is not visibly broken. But unlike the equivalent checkout endpoint (`test_customer_checkout_rejects_other_customer_invoice`, `payments/tests.py:150`), there is no dedicated test proving a logged-in customer cannot submit a bank-transfer notice against another customer's invoice ID. This is the exact IDOR risk the supervisor requirement calls out, applied to a write path rather than a read path. | Add `test_customer_bank_transfer_notice_rejects_other_customer_invoice` mirroring the checkout test: log in as customer A, POST to the bank-transfer-notice endpoint with customer B's invoice pk, assert 404/403 and no `Payment`/notice created. |

No Critical findings in either part.

## Part A — Reports app detail

Scope: Finance, HR, Admin, Management reports in `reports/` (views, services logic embedded in `reports/views.py`), plus cross-checks against `invoicing/exports.py`, `payroll/services.py`, `payroll/views.py`, `invoicing/views.py`, `core/views.py`.

- **Date filters** — boundary handling (inclusive start/end) and invalid-input rejection (bad date strings, start > end) implemented at `reports/views.py:115-145`; covered by `test_*_rejects_invalid_date_range_and_preserves_values` across all three date-filterable reports. All passing.
- **Collection / outstanding / overdue totals** — consistent `OUTSTANDING_INVOICE_STATUSES` set and a shared `successful_payments_queryset()` helper used identically across `invoice_customer_report`, `payment_stripe_report`, and `invoicing/views.py:invoice_list`'s drill-down filter map. No double counting observed. Overdue requires both `status=OVERDUE` and `due_date__lt=today`, consistent with invoicing's own overdue derivation.
- **Refund totals** — correct convention (succeeded-only), see Finding #1 above for the coverage gap.
- **Payroll calculations** — CPF total in `payroll_report` (`reports/views.py:1335-1345`) independently reimplements the same age/earnings logic as `payroll/services.py:cpf_for_2026`, consistently using `basic_salary + allowances`; covered by `payroll/tests.py:PayrollReportPlacementAndAccessTests` with concrete numeric assertions (e.g. `S$6,050.00`, `total_payroll_amount_month == Decimal("6050")`).
- **Empty states** — driven by boolean flags (`has_report_data`, `has_data`); no division operations found in `reports/views.py`, so no divide-by-zero risk on zero-record periods. Empty-state tests pass for both invoice and payroll reports.
- **Drill-down links** — every `{% url %}` / `reverse()` target used in the four report templates/views resolves to a real URL name (verified against `urls.py` across `accounts`, `core`, `invoicing`, `payroll`).
- **Charts** — spot-checked aggregation queries feeding chart context; consistent with totals logic above.
- **Previous-period comparisons** — `previous_month` quick-range (`reports/views.py:158-160`) and the Management dashboard's `_currency_delta_note` (`core/views.py:196-202, 260-262`) compute non-overlapping, correctly bounded prior-period date ranges.

## Part B — Customer portal detail

Scope: `invoicing/views.py` (customer dashboard/detail/download), `payments/views.py` + `payments/services.py` (Stripe checkout, webhook, bank-transfer), `invoicing/services.py` (overdue status), `invoicing/exports.py` (refund convention reference), and their test files.

### A) IDOR / ownership scoping

| Endpoint | Scoping mechanism | Test coverage |
|---|---|---|
| `customer_invoice_dashboard` (`invoicing/views.py:534`) | `_get_customer_invoice_queryset` → `Invoice.objects.filter(customer=linked_customer)`, `linked_customer` resolved via `InvoiceCustomer.objects.filter(email__iexact=email).first()` (`invoicing/views.py:239-251`) | Indirect (dashboard scoped by construction) |
| `customer_invoice_detail` (`invoicing/views.py:565`) | `get_object_or_404(scoped_invoices, pk=pk)`, 404s if no linked customer | Tested: `invoicing/tests.py:1807` `test_customer_cannot_view_other_customer_invoice` — passes |
| `customer_invoice_download_pdf` (`invoicing/views.py:589`) | Same `scoped_invoices` pattern | Same test also asserts PDF endpoint 404s |
| `checkout_customer_invoice` (`payments/views.py:402`) | `get_object_or_404(Invoice, pk=pk, customer__email__iexact=user_email)` | Tested: `payments/tests.py:150` `test_customer_checkout_rejects_other_customer_invoice` — passes |
| `submit_customer_bank_transfer_notice` (`payments/views.py:423-431`) | Identical `customer__email__iexact=user_email` filter | **No test** — see Finding #2 |
| `confirm_bank_transfer_payment_for_invoice` (`payments/views.py:511`) | Staff-only via `role_required(SUPERADMIN, ADMIN, FINANCE)` | Extensively tested |
| `checkout_success` / `checkout_cancel` | Looked up by opaque Stripe `session_id` / internal `payment_reference` (unguessable secret), not by user — acceptable design | Tested (`payments/tests.py:1692, 1811+`) |

### B) Stripe test payment flow
`checkout_success` (`payments/views.py:875`) finalizes via `finalize_checkout_success_from_redirect` as a sandbox fallback when the webhook hasn't fired yet, falling back to lookup by session ID. `stripe_webhook` (`payments/views.py:1011`) verifies the signature and calls `process_webhook_event` (`payments/services.py:1450`), which persists `StripeWebhookEvent` keyed by Stripe `event_id` with an `IntegrityError`-based idempotency guard (duplicate event → `created=False`, no reprocessing, no duplicate email). Covered by `test_webhook_completed_marks_invoice_paid`, `test_second_stripe_attempt_cannot_finalize_paid_invoice`, `test_webhook_duplicate_event_is_idempotent`, `test_webhook_completed_rejects_amount_mismatch`, `test_repeated_success_page_does_not_duplicate_payment_email`. No issues found.

### C) Bank-transfer submission + Finance verification
Customer submits via `submit_customer_bank_transfer_notice` → `get_or_create_bank_transfer_payment` + `submit_bank_transfer_notice` (`payments/services.py:308, 341`), which sets the payment to PENDING without marking the invoice paid (correctly requires Finance confirmation). Finance confirms via `confirm_bank_transfer_payment_for_invoice` → `confirm_bank_transfer_payment` (`payments/services.py:390`), validating amount/date and marking the invoice paid. Well tested (10+ methods: happy path, mismatched amount, future date, missing proof, already-paid-via-Stripe conflict). No issues found beyond Finding #2.

### D) Payment failure handling
`WEBHOOK_EVENT_ASYNC_FAILED` → payment status FAILED, invoice left untouched, audit log + failure email sent (`test_webhook_async_failed_marks_payment_failed_and_invoice_not_paid`). `WEBHOOK_EVENT_EXPIRED` → payment CANCELLED (`test_webhook_expired_marks_payment_cancelled_and_invoice_not_paid`). `checkout_cancel` also marks unfinished payments CANCELLED and sends a failure email. All well tested.

### E) Paid/overdue/refunded status display
`apply_overdue_status` / `refresh_overdue_invoices` (`invoicing/services.py:80-94`) correctly gate on `due_date < today` and `OVERDUE_ELIGIBLE_STATUSES`, invoked on every customer dashboard/detail load. Refund totals in `invoicing/exports.py:144` filter `refund.status == PaymentRefund.STATUS_SUCCEEDED` — the convention Part A's report totals correctly match (Finding #1 is about missing test coverage, not incorrect logic). Templates use `invoice.get_status_display` uniformly across paid/overdue/refunded/partially-refunded states, no special-casing bugs found.

## Overall assessment

**READY** (with 2 Important coverage gaps to close before sign-off, no Critical issues).

Both supervisor requirements are correctly implemented:
- Reports app: date filters, totals (collection/outstanding/overdue/refund), payroll calculations, empty states, drill-down links, charts, and previous-period comparisons all check out against code and passing tests, except refund-total mixed-status coverage is missing (Finding #1).
- Customer portal: ownership scoping is consistently enforced via customer-email-matched querysets across all read endpoints and the primary write (checkout) endpoint; Stripe and bank-transfer flows are idempotent and thoroughly tested; status derivation is correct. The one gap is a missing IDOR test for the bank-transfer-notice write endpoint (Finding #2) — code inspection shows it uses the same guard pattern as the tested checkout endpoint, so risk is assessed as coverage-only, not a live vulnerability.

Recommend adding the two tests above before declaring the branch fully evaluation-ready; no code changes are required unless those new tests reveal unexpected behavior.

## Fix report

Both coverage gaps closed. No production code was changed.

**Tests added:**
1. `reports/tests.py::PaymentStripeReportAccessTests.test_payment_report_refunded_amount_includes_only_succeeded_refunds` — creates three refunded invoices, each with one `PaymentRefund` (succeeded S$50.00, pending S$30.00, failed S$20.00) dated in the current period, and asserts `response.context["refunded_amount"] == Decimal("50.00")` (only the succeeded refund) and that the page renders `S$50.00`.
2. `payments/tests.py::StripePaymentsPhaseTests.test_customer_bank_transfer_notice_rejects_other_customer_invoice` — logs in as `customer_stripe` (linked to `self.customer`) and POSTs a bank-transfer notice to `payment-bank-transfer-notice-customer` for another customer's invoice; asserts a 404 and that no `PaymentRecord` was created for that invoice.

**Commands run (foreground):**
```
.venv/Scripts/python.exe manage.py test reports.tests.PaymentStripeReportAccessTests payments.tests.StripePaymentsPhaseTests --settings=config.test_settings
```

**Output summary:** `Ran 80 tests in 135.217s — OK`. Both new tests passed on first run, confirming the report's refund total and the bank-transfer-notice endpoint's ownership scoping behave correctly as-is; no production code changes were required.

