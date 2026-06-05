# AGENT.md

## Project
Automated Invoicing & Payroll System (Xero-inspired, not a direct clone)

## Main Goal
Build and harden a web-based internal business system that automates invoicing, payroll, bulk uploads, PDF/Excel generation, reminders, payment tracking, Stripe payment updates, and reporting.

The project has moved past the initial foundation-only stage. Treat the current codebase as an implemented Django application that now needs verification, cleanup, documentation, and careful bug fixing rather than broad new feature expansion.

The system should stay clean, reliable, maintainable, and scalable. Prioritize correctness, auditability, data integrity, security, and business workflow usability over visual flourish or unnecessary architecture.

---

## Current Project Status

### Verified
- `python manage.py check` passes with no system check issues.
- The project is a Django app using the expected app split:
  - `accounts`
  - `core`
  - `invoicing`
  - `payroll`
  - `imports`
  - `notifications`
  - `payments`
  - `reports`
  - `templates`
  - `static`

### Verification Gap
- A full `python manage.py test` run timed out after about two minutes during the latest handoff check. Do not mark the project fully validated until tests are run successfully or narrowed down and fixed.

### Implemented Or Partly Implemented
- Foundation project structure, Django settings, templates, static assets, and app routing.
- Environment-variable based configuration with SQLite local default and MySQL support through env settings.
- WhiteNoise static file handling and `build.sh` support.
- Authentication, email-or-username login backend, managed accounts, admin account management, role helpers, and role-based access patterns.
- Audit log model and audit helpers.
- Core dashboards and finance/customer entry surfaces.
- Invoicing models, forms, views, services, templates, invoice items, statuses, public invoice view tokens, view tracking, CSV upload preview, PDF/Excel exports, invoice email templates, and customer invoice views.
- Payroll employee models, payroll batch/entry models, payslip-related records, employee management, upload previews, payroll dashboards, and self-service payslip views.
- Import job and import error tracking models.
- Notification email logs, mass email/admin email views, payment reminder settings, and payment reminder management command.
- Stripe payment models, checkout success/cancel templates, payment service code, webhook event model, and payment email templates.
- Reporting templates for invoice/customer, payroll, Stripe/payment, and admin/security reports, with Chart.js-oriented static JavaScript.
- Database setup notes and migration/legacy table mapping documentation.

### Current Active Phase
Phase 12: Hardening, cleanup, verification, and handover.

Do not restart from Phase 1. Earlier phases should be treated as already implemented unless testing or inspection proves otherwise.

---

## Recommended Stack
Use this stack unless there is a strong technical reason not to:

- Backend: Python + Django
- Database: SQLite for local development by default, MySQL for production or shared database work
- Frontend: Django templates + Bootstrap
- Static files: WhiteNoise
- PDF generation: WeasyPrint if already wired, otherwise keep document generation within current project patterns
- Excel handling: openpyxl
- CSV/Excel validation and import: pandas/openpyxl where useful
- Email delivery: Django email backend configured by environment variables
- Payments: Stripe + webhook handling
- File storage: local in development, configurable for production
- Charts/report visuals: Chart.js

Do not introduce React, Vue, microservices, or extra infrastructure unless clearly needed.

Celery/Redis were part of the original future direction, but the current project uses a Django management command for payment reminders. Only add Celery/Redis if explicitly requested or if the background job requirement is re-scoped.

---

## Product Direction
This system is Xero-inspired in workflow and professionalism, but must not directly copy Xero branding, templates, colors, or protected design elements.

Documents and UI should be:
- professional
- clean
- modern
- business-friendly
- easy to audit
- structured similarly to common invoicing/payroll tools without cloning one exactly

---

## Core Modules And Expected State

### 1. Authentication And Role Management
Expected:
- secure login/logout
- email-or-username login
- managed account creation
- admin password/account tools
- role-based access checks for Admin, Finance, HR, Staff, and Customer-style flows where present
- audit trail for important actions

Next focus:
- verify permissions route by route
- ensure sensitive payroll, payment, and admin views are protected
- ensure failed-login/suspicious activity handling is consistent

### 2. Invoicing
Expected:
- customer records
- invoice list/create/detail/edit/delete where implemented
- automatic invoice numbering
- item calculations
- invoice statuses including Draft, Pending Payment/Sent, Viewed, Paid, Overdue, Refunded
- public invoice view page with token and view tracking
- PDF and Excel output
- invoice email sending and templates
- CSV upload preview/source row support

Next focus:
- verify totals, status transitions, public token access, email idempotency, and delete restrictions
- avoid changing business rules unless tests or user instructions require it

### 3. Payroll
Expected:
- employee records with Singapore-oriented profile fields
- payroll batch and entry models
- payroll record/payslip record support
- CSV/Excel upload previews
- payroll dashboard/list/detail/forms
- employee self-service payslip view

Next focus:
- verify payroll upload validation, payslip access restrictions, calculations, and naming/table consistency around legacy payslip records versus current payroll records

### 4. Bulk Upload And Data Validation
Expected:
- import job and row error models
- invoice/payroll upload preview flows
- validation error display template

Next focus:
- verify row-level error reporting, partial-save behavior, import job status updates, and downloadable error report support if claimed by UI

### 5. Email, Reminders, And Alerts
Expected:
- email delivery log model
- invoice email templates
- mass email/admin email surfaces
- payment reminder settings
- `send_payment_reminders` management command

Known follow-ups from `FOLLOW_UPS.md`:
- provider webhook ingestion for final email delivery events
- richer email delivery status model
- duplicate-send guard on invoice send action
- explicit resend flow with reason/audit trail
- webhook signature verification and replay protection for email provider events if added
- operational alerts for failed/suppressed/complained events
- reconciliation checks between local logs and provider events

### 6. Online Payments
Expected:
- Stripe payment record support
- checkout flow/service support
- success/cancel pages
- webhook event logging and duplicate event protection by event ID
- invoice/payment status updates
- refund-related statuses/templates where implemented

Next focus:
- verify webhook signature handling, duplicate webhook idempotency, failure/refund flows, and Stripe env variable documentation

### 7. Reporting And Dashboards
Expected:
- core dashboard
- invoicing dashboard
- payroll dashboard
- finance console
- report templates for invoice/customer, payroll, Stripe/payment, and admin/security areas
- chart support through `static/js/report_charts.js`

Next focus:
- verify report numbers, permissions, query efficiency, empty states, and chart rendering

---

## Working Style Rules
Follow these rules at all times:

### General Rules
- Do not rewrite the whole project unless necessary.
- Make focused, safe, incremental changes.
- Preserve working features.
- Prefer clear architecture over clever shortcuts.
- Keep naming consistent with the existing codebase.
- Keep files organized by responsibility.
- Treat existing migrations and legacy table mappings carefully.

### Code Quality Rules
- Write readable, maintainable code.
- Use descriptive names.
- Keep views thin where practical.
- Put business logic in services or clearly separated modules.
- Validate all user input.
- Handle errors gracefully.
- Add logging where useful.
- Avoid duplicated logic.
- Avoid hardcoded secrets, API keys, and magic values.

### UI Rules
- Keep the UI clean and business-like.
- Use Bootstrap and existing templates/CSS patterns.
- Make forms and tables easy to use.
- Prioritize clarity over visual complexity.
- Keep invoice and payslip templates professional.

### Data Rules
- Preserve normalized models and existing table names unless a migration is explicitly needed.
- Add indexes where helpful, but avoid churn.
- Use transactions for critical writes.
- Protect audit-related data.
- Never save invalid uploaded rows silently.
- Be careful with legacy database alignment and migrations documented in `DATABASE_SETUP_NOTES.md`.

### Security Rules
- Enforce authentication and permissions.
- Protect sensitive payroll routes.
- Validate file uploads.
- Sanitize exported and rendered data.
- Protect webhook endpoints.
- Store secrets only in environment variables.
- Do not expose `.env` values in logs, templates, or documentation.

---

## Current Priority Order
The next agent should work in this order:

1. Run focused verification and identify failing or slow tests.
2. Review permissions for admin, finance, HR, staff, customer, payroll, payment, and public invoice routes.
3. Verify invoicing totals, status transitions, email sending, public view tokens, and exports.
4. Verify payroll upload validation, employee access restrictions, and payslip generation.
5. Verify Stripe checkout/webhook/refund behavior and idempotency.
6. Verify import job/error tracking and reporting metrics.
7. Update setup/handover documentation after fixes.
8. Only then consider polish or optional enhancements.

---

## Phase Status

### Phase 1: Foundation
Status: Implemented.

Includes project structure, settings, env config, apps, authentication, roles, base templates, static handling, dashboard, and audit log support.

### Phase 2: Core Data Models
Status: Implemented.

Includes customers, invoices, invoice items, payment records, webhook events, employees, payroll batches/entries, payslip/payroll records, import jobs/errors, email logs, reminder settings, and audit logs.

### Phase 3: Invoicing MVP
Status: Implemented; verify and harden.

### Phase 4: Invoice PDF And Excel Output
Status: Implemented; verify generated output and exact totals.

### Phase 5: Invoice Email Sending
Status: Implemented; follow-ups remain in `FOLLOW_UPS.md`.

### Phase 6: Payroll MVP
Status: Implemented; verify upload validation and access controls.

### Phase 7: Payroll PDF Output
Status: Implemented or partly implemented; verify actual PDF generation before claiming complete.

### Phase 8: Bulk Import Framework
Status: Partly implemented; import models and upload previews exist. Verify reusable service separation, import history visibility, row-level errors, and downloadable error reporting.

### Phase 9: Background Jobs And Reminders
Status: Partly implemented without Celery/Redis.

Payment reminders are represented by settings and a Django management command. Celery/Redis are not currently required unless re-scoped.

### Phase 10: Stripe Payments
Status: Implemented; verify end-to-end Stripe checkout, webhook signature handling, duplicate event safety, failed/cancelled/refund flows, and documentation.

### Phase 11: Reporting Dashboard
Status: Implemented; verify metrics and permissions.

### Phase 12: Hardening And Cleanup
Status: Current active phase.

Focus on tests, route security, validation, documentation, dead code, naming consistency, and handover readiness.

---

## Architecture Guidance
Use this project structure:

- `accounts`
- `core`
- `invoicing`
- `payroll`
- `imports`
- `notifications`
- `payments`
- `reports`
- `templates`
- `static`

Suggested responsibility split:

- models for database structure
- forms for input validation
- services for business logic
- views for request handling
- management commands for scheduled/operational jobs currently used by the app
- tasks only if a background task framework is introduced intentionally
- utils only for small shared helpers

Do not place complex business logic directly in templates or views.

---

## Priority Rules For The Agent
Always prioritize work using this order:

1. correctness
2. security
3. data integrity
4. maintainability
5. usability
6. performance
7. visual polish

Do not optimize for visual polish before the workflow is correct.

---

## Definition Of Success
The project is successful when:

- users can log in with proper roles
- permissions are correct for every sensitive route
- invoices can be created, viewed, exported, emailed, paid, refunded where supported, and tracked
- payroll data can be uploaded and validated
- payslips can be generated and viewed securely
- reminders can be sent or triggered reliably
- payments update invoice status correctly and idempotently
- reporting is accurate
- setup and environment variables are documented
- tests pass or any remaining test gaps are explicitly documented
- the codebase is clean enough for another developer to continue easily

---

## Important Constraints
- Do not directly copy Xero branding or exact template design.
- Keep document layout inspired by professional accounting software only.
- Do not add unnecessary features before hardening existing workflows.
- Do not introduce major dependencies unless they clearly solve a real need.
- Do not break completed phases while cleaning up later phases.
- Do not reset migrations or legacy table mappings without explicit approval.

---

## Output Expectations For Every Task
Whenever working on a task, always return:

### 1. What was changed
State exactly which files or modules were created or updated.

### 2. Why it was changed
Explain the reason in relation to the project goals.

### 3. What is complete
State what now works.

### 4. What still remains
State the next logical step.

### 5. Risks or assumptions
Mention any assumption, limitation, or follow-up need.

---

## Current Assignment For The Agent
Work on Phase 12 only unless the user gives a narrower bug or feature request.

Do the following first:
- investigate why the full test run timed out
- run focused app-level tests where useful
- fix blocking test failures or slow tests
- review route permissions and sensitive data access
- verify Stripe webhook/payment idempotency
- verify invoice and payroll export/generation paths
- update README/setup docs after hardening

Do not start a new product expansion until the implemented workflows are verified and documented.

---

## Final Instruction
Work step by step.
Do not restart completed foundation work.
Do not overcomplicate the stack.
Prefer a stable, clean, production-like student project over an overly ambitious design.
