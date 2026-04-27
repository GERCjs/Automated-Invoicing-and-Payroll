# TASK.md

## Purpose
This file tells the agent exactly what to work on, in what order, and what counts as completion.

The agent must follow `AGENT.md` first.
This file is the execution checklist.

---

# Global Rules
These rules apply to every phase.

## Rules
- Work in small safe steps.
- Do not jump ahead to later phases.
- Do not rewrite unrelated code.
- Do not add unnecessary packages.
- Do not change naming conventions without reason.
- Do not break already completed work.
- Keep code readable and maintainable.
- Keep business logic out of templates.
- Keep views thin where possible.
- Validate all inputs.
- Log important system actions.
- Report assumptions clearly.

## After every phase, always provide
- what files were created
- what files were updated
- what features now work
- what still does not exist yet
- what the next phase should be

---

# Phase 1 â€” Foundation Setup

## Goal
Create a stable Django project base that supports future invoicing and payroll work.

## Priority
Highest

## Tasks
- create Django project
- create base apps structure
- configure MySQL
- configure environment variables
- create base settings split if needed
- configure static and template folders
- install and configure Bootstrap
- create base layout template
- implement login and logout
- create role structure for Admin, Finance, HR, Staff
- create basic protected dashboard page
- create audit log model
- create helper or middleware approach for audit logging where appropriate
- set up custom user model only if clearly beneficial from the start
- prepare app folders for future modules:
  - accounts
  - core
  - invoicing
  - payroll
  - imports
  - notifications
  - payments
  - reports

## Deliverables
- running Django project
- working MySQL connection
- environment variable support
- basic authentication flow
- role-protected pages
- reusable base template
- audit log model
- clean folder structure

## Acceptance Criteria
Phase 1 is complete only when:
- project starts without error
- migrations run successfully
- login works
- logout works
- a user can be restricted by role
- base navigation exists
- dashboard page exists
- audit log model exists
- project structure is ready for future modules

## Do Not Do Yet
- invoicing logic
- payroll logic
- Stripe integration
- Celery and Redis setup
- dashboard charts
- file import parsing
- PDF generation
- Excel export

---

# Phase 2 â€” Core Data Model Design

## Goal
Define the main database models before building workflows.

## Priority
Only after Phase 1 is stable

## Tasks
Create and relate models for:
- client or customer
- employee
- invoice
- invoice item
- invoice view tracking if needed
- payment record
- payroll batch
- payroll entry
- payslip record
- import job
- import row error
- email delivery log
- audit log refinement if needed

Define:
- statuses
- timestamps
- ownership or created by tracking
- proper foreign key relationships
- indexes where useful

## Deliverables
- migrations for all key models
- admin registration for inspection
- clear model structure

## Acceptance Criteria
Phase 2 is complete only when:
- all core models exist
- relationships are valid
- status fields are defined clearly
- migrations apply cleanly
- admin can inspect records
- no obvious duplicated model purpose exists

## Do Not Do Yet
- full invoice workflow
- payroll upload workflow
- reminder jobs
- payment processing

---

# Phase 3 â€” Invoicing MVP

## Goal
Build the first complete business workflow.

## Priority
Build before payroll

## Tasks
- create invoice list page
- create invoice creation form
- create invoice detail page
- create invoice edit flow if needed
- implement invoice item calculations
- implement auto invoice numbering
- implement invoice statuses:
  - Draft
  - Sent
  - Viewed
  - Paid
  - Overdue
- create customer selection flow
- create online invoice view page
- mark invoice as viewed when accessed
- implement overdue determination
- record audit log entries for key invoice actions

## Deliverables
- usable invoicing workflow
- invoice records saved correctly
- status changes handled properly

## Acceptance Criteria
Phase 3 is complete only when:
- finance or admin can create invoice
- totals calculate correctly
- invoice number generates correctly
- invoice detail page works
- status transitions make sense
- invoice online view page works
- viewed status can be tracked
- overdue logic can be determined

## Do Not Do Yet
- Stripe
- mass reminders
- payroll upload
- bulk import engine
- advanced charts

---

# Phase 4 â€” Invoice PDF and Excel Output

## Goal
Generate business-ready invoice documents.

## Priority
After invoice workflow exists

## Tasks
- create invoice PDF template
- create invoice Excel export
- style PDF professionally
- style Excel clearly and cleanly
- keep layout Xero-inspired, not copied exactly
- include:
  - company info
  - customer info
  - invoice number
  - issue date
  - due date
  - item table
  - totals
  - notes if applicable
- ensure totals match stored data exactly

## Deliverables
- downloadable invoice PDF
- downloadable invoice Excel

## Acceptance Criteria
Phase 4 is complete only when:
- PDF downloads successfully
- Excel downloads successfully
- values match invoice data exactly
- layout is professional and readable
- documents are suitable for internal or client use

## Do Not Do Yet
- payroll documents
- Stripe
- mass reminder jobs

---

# Phase 5 â€” Invoice Email Sending

## Goal
Allow invoices to be sent properly and tracked.

## Priority
After invoice document generation

## Tasks
- configure email backend
- create invoice email template
- add send invoice action
- add online invoice link in email
- add delivery log model usage
- update invoice status to Sent when appropriate
- record failures safely
- make sending logic reusable for future payroll emails

## Deliverables
- invoice email sending
- email logs
- status update on send

## Acceptance Criteria
Phase 5 is complete only when:
- invoice can be emailed
- email content is correct
- delivery attempt is logged
- send failures do not corrupt invoice data
- sent status works as intended

---

# Phase 6 â€” Payroll MVP

## Goal
Build payroll upload and payslip generation workflow.

## Priority
Only after invoicing is already functional

## Tasks
- create employee management pages if needed
- create payroll batch upload page
- accept CSV and Excel
- parse file content
- validate required fields
- reject invalid rows clearly
- save valid rows safely
- create payroll batch history
- generate payslip records
- create employee self-service payslip view
- protect employee data access properly

## Deliverables
- payroll upload workflow
- validation feedback
- payslip records
- secure employee access

## Acceptance Criteria
Phase 6 is complete only when:
- HR or admin can upload payroll file
- invalid rows are shown clearly
- valid rows are stored correctly
- payslips can be generated
- employee only sees their own payslip

---

# Phase 7 â€” Payroll PDF Output

## Goal
Generate professional payslips.

## Priority
After payroll upload works

## Tasks
- create payslip PDF template
- include employee details
- include period details
- include earnings
- include deductions
- include net pay
- keep style professional and readable
- ensure values match stored payroll records

## Deliverables
- downloadable payslip PDF

## Acceptance Criteria
Phase 7 is complete only when:
- payslip PDF generates without errors
- payroll values are correct
- output is readable and professional
- access is properly restricted

---

# Phase 8 â€” Bulk Import Framework

## Goal
Create a reusable import engine for invoices and payroll.

## Priority
After both invoicing and payroll MVPs exist

## Tasks
- separate parsing logic
- separate validation logic
- separate save logic
- create import job record
- create import error row record
- support downloadable error reporting
- support clear import summary:
  - total rows
  - valid rows
  - invalid rows
  - saved rows

## Deliverables
- reusable import service layer
- better validation reporting

## Acceptance Criteria
Phase 8 is complete only when:
- invoice and payroll imports use reusable patterns
- import history is visible
- row-level errors are readable
- no silent failure occurs

---

# Phase 9 â€” Background Jobs and Reminders

## Goal
Automate sending and reminder workflows.

## Priority
After email sending is stable

## Tasks
- configure Celery
- configure Redis
- move long-running send jobs to background tasks
- create overdue reminder job
- create retry-safe tasks
- prevent duplicate reminders
- log reminder attempts

## Deliverables
- background email sending
- reminder scheduler
- task logging where useful

## Acceptance Criteria
Phase 9 is complete only when:
- async jobs run correctly
- overdue reminders can be triggered
- duplicate sends are controlled
- failures are visible and manageable

---

# Phase 10 â€” Stripe Payments

## Goal
Allow invoice payments and automatic status updates.

## Priority
After invoices are stable

## Tasks
- create payment start flow
- link Stripe payment session to invoice
- create success and cancel handling
- create secure webhook endpoint
- verify webhook signature
- update invoice payment status from webhook
- log webhook events
- prevent duplicate payment state corruption

## Deliverables
- Stripe invoice payment flow
- automatic payment status updates

## Acceptance Criteria
Phase 10 is complete only when:
- payment flow works end to end
- successful payment marks invoice correctly
- duplicate webhook handling is safe
- failed or cancelled flow does not break invoice state

---

# Phase 11 â€” Reporting Dashboard

## Goal
Create useful reporting for business users.

## Priority
After the main workflows work

## Tasks
- create dashboard homepage
- show invoice counts by status
- show payment summary
- show payroll summary
- show recent uploads
- show recent sends
- show recent activity
- add simple charts only where useful
- keep page clean and fast

## Deliverables
- operational dashboard
- summary widgets
- useful recent activity visibility

## Acceptance Criteria
Phase 11 is complete only when:
- data shown is accurate
- page is understandable
- metrics load efficiently
- visuals support business decisions

---

# Phase 12 â€” Hardening and Cleanup

## Goal
Make the project safer and easier to hand over.

## Priority
Final phase

## Tasks
- review permissions
- review validation
- review model constraints
- review error handling
- review logging
- remove dead code
- improve naming consistency
- improve comments only where needed
- add seed or demo data if requested
- update README
- document setup steps
- document environment variables
- document background job setup
- document Stripe webhook setup

## Deliverables
- cleaned and documented project
- reduced technical debt
- easier setup for next developer

## Acceptance Criteria
Phase 12 is complete only when:
- setup is documented
- key modules are understandable
- risks are reduced
- handover quality is acceptable

---

# Current Active Task
Start here first unless manually changed.

## Active Phase
Phase 1 â€” Foundation Setup

## Exact Instruction
Work on Phase 1 only.

Focus only on:
- Django project initialization
- MySQL configuration
- environment variable setup
- base apps structure
- authentication
- role-based access control
- Bootstrap base layout
- protected dashboard page
- audit log model

Do not start later phases yet.

---

# Next Task Template
Use this template whenever assigning the next phase to the agent.

## Template
Read `AGENT.md` and `TASK.md` and follow them strictly.

Work on Phase X only.

Scope:
- item 1
- item 2
- item 3

Do not work on any later phase items.

Make focused incremental changes only.

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
