# Task 1: Management Dashboard template rebuild + remove CEO Dashboard

## Goal
Make the Management Dashboard at `/dashboard/` display everything the supervisor requires, and remove the duplicate "CEO Dashboard" page entirely. Only the name "Management Dashboard" may remain.

## The spec = the 6 failing tests
Run first to see them fail (uses SQLite test settings; the default settings point at an unreachable Azure MySQL):

```
.venv/Scripts/python.exe manage.py test core --settings=config.test_settings
```

Failing tests, all in `core/tests.py` class `CoreCollectionReportingTests`:
- test_management_dashboard_excludes_drafts_from_ceo_summary (~:227)
- test_management_dashboard_is_reserved_for_management_roles (~:732)
- test_management_dashboard_keeps_payroll_burden_off_ceo_snapshot (~:264)
- test_management_dashboard_shows_previous_month_collection_comparison (~:308)
- test_management_dashboard_shows_purpose_text_clear_links_and_empty_attention_state (~:381)
- test_management_dashboard_surfaces_submitted_bank_transfers_for_verification (~:698)

Read each test carefully — they assert exact strings/ids that must appear (or must NOT appear) in the rendered `/dashboard/` HTML. Treat the test assertions as the verbatim requirements.

## Situation
- View `dashboard()` at `core/views.py:459` already computes the full context (`core/views.py:472-806`): `collected_this_month`, `collected_previous_month`, `collection_comparison_note`, `previous_month_label`, `invoice_outstanding`, `refunded_total`, `secondary_summary_items` (Overdue Amount / Payment Issues / Import Issues), operational risk counts, `submitted_bank_transfer_count`, `collection_chart_summary`, `detail_report_links`, management attention items, etc. **Do not change the computed values — the fix is template-side rendering.** Minor view tweaks (e.g. link label text like "View Finance Report", purpose/period strings) are fine if that's where the strings live.
- Template `templates/core/dashboard.html` is a 162-line stub: it renders only Top Collection Risks, Management Attention, and a chart `<script>` that references `canvasId:"collectionTrendChart"` / `emptyStateId:"collectionTrendChartEmpty"` — but no such `<canvas>`/empty-state element exists in the markup.
- A complete reference implementation exists: `templates/core/ceo_dashboard.html` (view `ceo_dashboard` at `core/views.py:810`, context builder `_build_ceo_dashboard_context` at `core/views.py:278`, URL `ceo-dashboard` in `core/urls.py:9`, nav link `templates/base.html:70`). Reuse its markup/structure for the dashboard where it matches the test expectations.

## Required work
1. Rebuild `templates/core/dashboard.html` so `/dashboard/` renders (per the tests): purpose text "CEO view of cash collection, issued receivables, operational risk, and drill-down reports.", "Reporting period:", "CEO Health Snapshot" KPI grid (`report-kpi-grid`) with "Collected This Month", previous-month comparison, "Issued Outstanding" (with caption "Sent, viewed, and overdue invoices waiting for customer payment."), secondary summary ("Overdue Amount", "Payment Issues", "Import Issues"), "Operational Risk Items" section incl. submitted bank transfer counts, "Collection Trend" section with the canvas + empty state ("No collection trend data is available yet.", `report-chart-summary`), report links labeled "View Finance Report" / "View Payment Report" etc., and the caption "Payroll details stay in the Payroll Report." while NOT showing "Payroll Burden This Month" / "Cash After Payroll". Keep the existing Top Collection Risks and Management Attention tables (`report-attention-table`) working.
2. Remove the CEO Dashboard as a separate page: delete URL `ceo-dashboard` from `core/urls.py`, the `ceo_dashboard` view, `templates/core/ceo_dashboard.html`, and the nav link in `templates/base.html`. Keep `_build_ceo_dashboard_context` ONLY if `dashboard()` uses it; otherwise remove it too. Search the whole repo for `ceo-dashboard` / `ceo_dashboard` references (templates, redirects, tests, docs) and clean them up.
3. Existing tests that target the removed CEO dashboard page (core/tests.py ~:427, :448, :622, :761): if their assertions describe content that now lives on /dashboard/, repoint them at `reverse("dashboard")`; delete only assertions/tests that are strictly about the separate page existing.
4. Do NOT weaken or delete any of the 6 failing management-dashboard tests — they must pass as written.

## Done criteria
- `.venv/Scripts/python.exe manage.py test core invoicing --settings=config.test_settings` → core fully green (the pre-existing invoicing refund failure `test_fully_refunded_invoice_shows_refund_amount_and_zero_due` is out of scope for this task and expected to still fail; everything else green).
- `grep -ri "ceo.dashboard" --include="*.py" --include="*.html"` returns no live page/URL references (strings inside the management dashboard like "CEO Health Snapshot" are fine — the tests require them).
- Commit your work on the current branch (fix/final-evaluation) with a clear message.
