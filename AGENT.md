# AGENT.md

## Project
Automated Invoicing & Payroll System (Xero-inspired, not a direct clone)

## Main Goal
Build a web-based internal business system that automates invoicing, payroll, bulk uploads, PDF/Excel generation, reminders, payment tracking, and reporting.

The system should be clean, reliable, maintainable, and scalable. It must prioritize correctness, auditability, and business workflow usability over unnecessary complexity.

---

## Recommended Stack
Use this stack unless there is a strong technical reason not to:

- Backend: Python + Django
- Database: MySQL
- Frontend: Django templates + Bootstrap
- Background jobs: Celery + Redis
- PDF generation: WeasyPrint
- Excel handling: openpyxl
- CSV/Excel validation and import: pandas
- Email delivery: SMTP or SendGrid
- Payments: Stripe + webhook handling
- File storage: local in development, configurable for production
- Charts/report visuals: Chart.js

Do not introduce React, Vue, microservices, or extra infrastructure unless clearly needed.

---

## Product Direction
This system is Xero-inspired in workflow and professionalism, but must not directly copy Xero branding or protected design elements.

The output documents should be:
- professional
- clean
- modern
- business-friendly
- structured similarly to common invoicing/payroll tools

---

## Core Modules
The system must support the following:

### 1. Authentication and Role Management
Roles:
- Admin
- Finance
- HR
- Staff

Requirements:
- secure login
- role-based access control
- permission checks
- audit trail for important actions

### 2. Invoicing
Requirements:
- manual single invoice creation
- automatic invoice numbering
- invoice status tracking
- PDF generation
- Excel export
- email invoice to recipient
- online invoice view page
- statuses: Draft, Sent, Viewed, Paid, Overdue

### 3. Payroll
Requirements:
- upload payroll data from CSV or Excel
- validate uploaded data
- generate payslips as PDF
- employee self-service portal
- optional support for payroll calculations such as allowances and deductions

### 4. Bulk Upload and Data Validation
Requirements:
- bulk upload invoice data
- bulk upload payroll data
- validate rows before saving
- show clear import error reporting
- allow successful rows to be tracked cleanly

### 5. Email, Reminders, and Alerts
Requirements:
- send invoices and payslips by email
- support mass sending
- reminder scheduling
- delivery logging
- optional alert support for WhatsApp later, but do not build first unless requested

### 6. Online Payments
Requirements:
- Stripe payment integration
- invoice payment page
- webhook-based payment confirmation
- update payment status automatically

### 7. Reporting and Dashboards
Requirements:
- invoice summary metrics
- payroll summary metrics
- validation/import reports
- status breakdowns
- recent activity

---

## Working Style Rules
Follow these rules at all times:

### General Rules
- Do not rewrite the whole project unless necessary.
- Make focused, safe, incremental changes.
- Preserve working features.
- Prefer clear architecture over clever shortcuts.
- Keep naming consistent.
- Keep files organized by responsibility.

### Code Quality Rules
- Write readable, maintainable code.
- Use descriptive names.
- Keep views/controllers thin.
- Put business logic in services or clearly separated modules.
- Validate all user input.
- Handle errors gracefully.
- Add logging where useful.
- Avoid duplicated logic.
- Avoid hardcoded secrets, API keys, and magic values.

### UI Rules
- Keep the UI clean and business-like.
- Use Bootstrap for consistency and speed.
- Make forms and tables easy to use.
- Prioritize clarity over visual complexity.
- Keep invoice and payslip templates professional.

### Data Rules
- Use MySQL properly with normalized models.
- Add indexes where helpful.
- Use transactions for critical writes.
- Protect audit-related data.
- Never save invalid uploaded rows silently.

### Security Rules
- Enforce authentication and permissions.
- Protect sensitive payroll routes.
- Validate file uploads.
- Sanitize exported and rendered data.
- Protect webhook endpoints.
- Store secrets only in environment variables.

---

## What To Build First
Build in this exact order unless instructed otherwise.

---

## Phase 1: Foundation
### Goal
Create a stable base project structure that supports future features cleanly.

### Tasks
- initialize Django project
- configure MySQL connection
- configure environment variables
- set up Bootstrap layout
- create base template
- set up authentication
- create role system
- create core navigation
- create audit log model
- create reusable permission checks

### Done When
- project runs without errors
- login/logout works
- role-protected pages work
- database migrations run cleanly
- base layout is ready for feature pages

---

## Phase 2: Core Data Models
### Goal
Create all main models cleanly before building workflows.

### Tasks
Create models for:
- User profile or role mapping
- Client or customer
- Employee
- Invoice
- Invoice item
- Payment record
- Payroll batch
- Payroll entry
- Payslip record
- Import job
- Import error row
- Email log
- Audit log

### Done When
- all core models exist
- relationships are correct
- status fields are defined
- admin panel can inspect records
- migrations are stable

---

## Phase 3: Invoicing Module
### Goal
Deliver the first fully usable business workflow.

### Tasks
- create invoice create page
- create invoice detail page
- create invoice list page
- implement automatic invoice numbering
- implement invoice status flow
- generate invoice PDF
- generate invoice Excel export
- create online invoice view page
- add email sending for invoice
- mark invoice as viewed when online link is opened
- add overdue logic

### Done When
- finance/admin can create invoice
- invoice items calculate correctly
- PDF output works
- Excel export works
- invoice can be emailed
- status updates are accurate

---

## Phase 4: Payroll Module
### Goal
Build payroll upload and payslip generation.

### Tasks
- create employee records
- create payroll upload page
- parse CSV and Excel uploads
- validate payroll rows
- store payroll batch history
- generate payslips
- create employee self-service view
- optionally support allowance and deduction calculations if structure is confirmed

### Done When
- HR/admin can upload payroll file
- invalid rows are reported clearly
- valid rows are saved safely
- payslip PDF generation works
- employee can view their own payslip only

---

## Phase 5: Bulk Upload and Validation Layer
### Goal
Make import handling robust and user-friendly.

### Tasks
- create import service layer
- separate parsing, validation, saving, and reporting
- create downloadable error report
- ensure partial failure handling is clear
- create import job status tracking

### Done When
- imports are traceable
- validation errors are readable
- no silent failures happen
- import history is visible

---

## Phase 6: Email and Reminders
### Goal
Automate outbound communication safely.

### Tasks
- configure email backend
- create email templates
- add email logs
- support invoice send
- support payslip send if needed
- configure Celery and Redis
- create reminder jobs for overdue invoices
- ensure retry-safe background tasks

### Done When
- email sending works
- logs are stored
- reminder jobs run correctly
- failures are visible and do not corrupt data

---

## Phase 7: Stripe Payments
### Goal
Allow invoice payments and automatic status updates.

### Tasks
- create Stripe checkout flow or payment link flow
- link payment to invoice
- implement webhook endpoint
- verify webhook signature
- update invoice payment status
- log webhook events

### Done When
- payment flow works end to end
- successful payment updates invoice correctly
- duplicate webhook events do not break state
- failed payments are handled safely

---

## Phase 8: Dashboard and Reporting
### Goal
Provide useful business visibility without overengineering.

### Tasks
- build dashboard homepage
- show invoice totals by status
- show payroll totals
- show recent imports
- show recent email activity
- show payment summary
- add charts where helpful
- keep filters simple and useful

### Done When
- dashboard loads efficiently
- metrics are accurate
- filters behave clearly
- visuals help business users understand data quickly

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
- tasks for background jobs
- utils only for small shared helpers

Do not place complex business logic directly in templates or views.

---

## Priority Rules for the Agent
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

## Definition of Success
The project is successful when:

- users can log in with proper roles
- invoices can be created, viewed, exported, emailed, and tracked
- payroll data can be uploaded and validated
- payslips can be generated and viewed securely
- reminders can be sent automatically
- payments can update invoice status correctly
- reporting is accurate
- the codebase is clean enough for another developer to continue easily

---

## Important Constraints
- Do not directly copy Xero branding or exact template design.
- Keep document layout inspired by professional accounting software only.
- Do not add unnecessary features before core workflows are complete.
- Do not introduce major dependencies unless they clearly solve a real need.
- Do not break existing completed phases while implementing later phases.

---

## Output Expectations for Every Task
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

## First Assignment for the Agent
Start with Phase 1 only.

Do the following first:
- set up Django project structure
- configure MySQL
- configure environment variables
- implement authentication
- implement role-based access control
- create a clean Bootstrap base layout
- create an audit log model
- prepare the project for invoicing and payroll apps

Do not start invoicing, payroll, Stripe, Celery, or dashboard work until Phase 1 is stable.

---

## Second Assignment After Phase 1
After Phase 1 is stable, move to Phase 2 and Phase 3 in order:
- define models
- then build invoicing workflow first

Payroll should come only after invoicing is already functioning.

---

## Final Instruction
Work step by step.
Do not skip foundation work.
Do not overcomplicate the stack.
Prefer a stable, clean, production-like student project over an overly ambitious design.
