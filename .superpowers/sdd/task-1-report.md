# Task 1 Report: Management Dashboard template rebuild + remove CEO Dashboard

## Status
DONE

## Commit
574390a3cbee78e072f9fa7edc9ba598840c0f15 — "Rebuild Management Dashboard template and remove duplicate CEO Dashboard page"

## What changed and why

### 1. Rebuilt `templates/core/dashboard.html`
The view already computed the full context; the template was a 162-line stub
missing most of it. Rebuilt it (reusing markup patterns from the now-removed
`ceo_dashboard.html`) to render:
- Header: page title, purpose text "CEO view of cash collection, issued
  receivables, operational risk, and drill-down reports.", "Reporting
  period:" / "Last updated:" meta, and the detail report link buttons.
- "CEO Health Snapshot" KPI grid (`report-kpi-grid`) with Collected This
  Month (+ previous-month comparison note), Issued Outstanding (with caption
  "Sent, viewed, and overdue invoices waiting for customer payment."), and a
  Refunded tile. A "Payroll details stay in the Payroll Report." caption sits
  next to the heading; no payroll-burden/cash-after-payroll figures appear.
- "Operational Risk Items" section rendering `secondary_summary_items`
  (Overdue Amount / Payment Issues / Import Issues), which already carries
  the submitted-bank-transfer note text and payment-report link.
- "Collection Trend" section with the `<canvas id="collectionTrendChart">`
  and `<div id="collectionTrendChartEmpty">` that the existing chart script
  was already referencing but that didn't exist before, plus a
  `report-chart-summary` stat strip (current month / 6-month total / peak
  month) driven by the existing `collection_chart_summary` context value.
- Kept the existing Top Collection Risks and Management Attention tables
  (`report-attention-table`) unchanged.

No computed values in `core/views.py` were changed for this section — only
template-side rendering, exactly as instructed.

### 2. Minor view tweak (`core/views.py`, `dashboard()`)
Changed the four `detail_report_links` labels from "Finance Report" /
"Payment Report" / "Payroll Report" / "Security Report" to "View Finance
Report" / "View Payment Report" / "View Payroll Report" / "View Security
Report" so the rendered buttons literally contain the strings the tests
assert ("View Finance Report", "View Payment Report", plus substring matches
for "Payroll Report" / "Security Report").

### 3. Removed the CEO Dashboard as a separate page
- `core/urls.py`: deleted the `ceo-dashboard/` path.
- `core/views.py`: deleted the `ceo_dashboard` view. Since `dashboard()`
  never called `_build_ceo_dashboard_context`, also removed that function
  and its now-orphaned helpers (`_build_ceo_service_items`,
  `_normalize_service_item_name`, `_money_decimal`, `_average_money`), and
  cleaned up now-unused imports (`InvoiceItem`, `Max`).
- `templates/core/ceo_dashboard.html`: deleted.
- `templates/base.html`: removed the "CEO Dashboard" nav link.

### 4. Test cleanup (`core/tests.py`)
Removed tests that were strictly about the standalone CEO Dashboard page
existing/rendering (their assertions describe content — Total Sales, Active
Customers, Top 5 High Spenders, `ceo-health-dashboard` id, etc. — that has
no equivalent on the merged Management Dashboard, and some explicitly assert
`assertNotContains(... "Management Attention")` / `"Operational Risk Items"`,
which would now be false since those sections are required on `/dashboard/`):
- `test_ceo_dashboard_requires_authentication`
- `test_ceo_dashboard_renders_company_health_profile`
- `test_ceo_dashboard_builds_company_health_metrics_from_paid_sales`
- `test_ceo_dashboard_empty_state_uses_zeroes_and_no_placeholders`
- `test_ceo_dashboard_is_reserved_for_management_roles`

Updated the role-based navigation test (`test_...` around nav links) to move
"CEO Dashboard" out of the expected-links list for superadmin/admin (since
the nav link no longer exists) and into the excluded-links list instead —
the other roles' excluded-links lists already contained "CEO Dashboard" and
needed no change.

The 6 target failing tests in `CoreCollectionReportingTests` were **not**
weakened or deleted; they pass as originally written.

## Test commands run

```
.venv/Scripts/python.exe manage.py test core.tests.CoreCollectionReportingTests --settings=config.test_settings
```
Result: **OK — 16/16 tests passed.**

```
.venv/Scripts/python.exe manage.py test core invoicing --settings=config.test_settings
```
Result: **185/186 passed.** The only failure is the pre-existing,
out-of-scope `invoicing.tests.InvoiceTemplateSettingsTests.test_fully_refunded_invoice_shows_refund_amount_and_zero_due`
(`AssertionError: Decimal('0.00') != Decimal('109.00')`), which the brief
explicitly calls out as belonging to Task 2, not Task 1.

## Verification
- `grep -rin "ceo.dashboard" --include="*.py" --include="*.html" .` now only
  matches `core/tests.py` lines that assert "CEO Dashboard" text is **absent**
  from nav for non-owning roles — no live page/URL/template references
  remain. "CEO Health Snapshot" (required by the tests) is unaffected since
  it doesn't match `ceo.dashboard`.
- `git diff --stat` for the commit: 6 files changed, 93 insertions(+), 765
  deletions(-) — `templates/core/ceo_dashboard.html` deleted, `core/tests.py`
  and `core/views.py` net negative from removing the duplicate page/tests,
  `templates/core/dashboard.html` net positive from the rebuild.

## Files touched
- `core/views.py`
- `core/urls.py`
- `core/tests.py`
- `templates/base.html`
- `templates/core/dashboard.html`
- `templates/core/ceo_dashboard.html` (deleted)

## Concerns
None. The single remaining failure in the `core invoicing` run is the
pre-existing refund-consistency bug explicitly assigned to Task 2 in the
5-task plan, not part of this task's scope.
