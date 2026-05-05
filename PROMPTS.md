# PROMPTS.md

## Purpose
This file gives you a prompt library with stable keys so you can tell an agent exactly what to do.

The agent must follow:
- `AGENT.md`
- `TASK.md`

---

## Quick Use (Recommended)
1. Choose a key from the map below (`P01`, `P02`, ..., `VERIFY`, `REFACTOR`, `BUGFIX`, `HANDOVER`).
2. Copy the `Dispatcher Prompt`.
3. Replace `PROMPT_KEY` with the prompt you want.
4. Send it to your coding agent.

### Prompt Key Map
- `P01` - Phase 1 Foundation Setup
- `P02` - Phase 2 Core Data Models
- `P03` - Phase 3 Invoicing MVP
- `P04` - Phase 4 Invoice PDF and Excel Output
- `P05` - Phase 5 Invoice Email Sending
- `P06` - Phase 6 Payroll MVP
- `P07` - Phase 7 Payroll PDF Output
- `P08` - Phase 8 Bulk Import Framework
- `P09` - Phase 9 Background Jobs and Reminders
- `P10` - Phase 10 Stripe Payments
- `P11` - Phase 11 Reporting Dashboard
- `P12` - Phase 12 Hardening and Cleanup
- `VERIFY` - Verification for current phase
- `REFACTOR` - Safe refactor for current phase
- `BUGFIX` - Bug fix for current phase
- `HANDOVER` - Final pre-handover review

---

## Dispatcher Prompt (Copy/Paste)
```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) PROMPTS.md

PROMPT_KEY: <PUT_KEY_HERE>

Rules:
- Execute only the prompt that matches PROMPT_KEY.
- If PROMPT_KEY is invalid or missing, stop and ask for a valid key.
- Do not start later phases.
- Make focused, safe, incremental changes only.

Output format:
1. What was changed
2. Why it was changed
3. What is complete
4. What still remains
5. Risks or assumptions
```

---

## Prompt Library

### `P01` - Phase 1 Foundation Setup
```text
Read AGENT.md and TASK.md and follow them strictly.

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

Requirements:
- make focused incremental changes only
- keep code readable and maintainable
- do not start invoicing, payroll, Stripe, Celery, Redis, imports, PDF generation, Excel export, or dashboard reporting
- do not overengineer the setup
- preserve a clean structure for later phases

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P02` - Phase 2 Core Data Models
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 2 only.

Focus only on defining the core data models and relationships for:
- client or customer
- employee
- invoice
- invoice item
- payment record
- payroll batch
- payroll entry
- payslip record
- import job
- import row error
- email delivery log

Requirements:
- define clear model relationships
- define clear status fields and timestamps
- add created by or ownership tracking where useful
- register models in admin for inspection
- keep migrations clean and stable
- do not build workflow pages yet
- do not implement invoice sending, payroll uploads, Stripe, Celery, PDF generation, or Excel export yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P03` - Phase 3 Invoicing MVP
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 3 only.

Build the first invoicing MVP workflow.

Focus only on:
- invoice list page
- invoice creation form
- invoice detail page
- invoice item calculations
- auto invoice numbering
- invoice status flow
- customer selection
- online invoice view page
- viewed tracking
- overdue logic
- audit log entries for invoice actions

Requirements:
- finance and admin roles should be able to manage invoices
- totals must calculate correctly
- status flow must make sense
- keep views clean and business logic organized
- do not build PDF, Excel export, email sending, Stripe, payroll, or reminders yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P04` - Phase 4 Invoice PDF and Excel Output
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 4 only.

Add invoice document output.

Focus only on:
- invoice PDF generation
- invoice Excel export
- professional layout and styling
- accurate data mapping from stored invoice data
- Xero-inspired structure without directly copying branding or exact design

PDF and Excel should include:
- company details
- customer details
- invoice number
- issue date
- due date
- item table
- totals
- notes if applicable

Requirements:
- values must match invoice data exactly
- PDF should be customer-facing and polished
- Excel should be clear and structured
- do not build email sending, payroll documents, Stripe, Celery, or reminders yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P05` - Phase 5 Invoice Email Sending
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 5 only.

Add invoice email sending and tracking with a provider decision matrix.

Step 1 - Create and use an email provider decision matrix before implementation.
Compare options that are realistic for this Django project:
- Resend via SMTP
- Resend via API
- Generic SMTP provider (for example SendGrid SMTP or SES SMTP)

Score each option from 1 to 5 on:
- Django integration effort
- Delivery visibility (events, bounces, failures)
- Logging fit with EmailDeliveryLog model
- Idempotency and retry safety
- Invoice status safety on send failure
- Reusability for future payroll email use
- Operational complexity and lock-in
- Cost predictability

Pick one option and state a short reason based on score and tradeoffs.

Step 2 - Implement Phase 5 only.
Focus only on:
- email backend configuration
- invoice email template (professional and clear)
- send invoice action from invoicing workflow
- online invoice public link inside email
- delivery attempt logging via EmailDeliveryLog
- update invoice status to Sent only when send succeeds
- failure-safe behavior (send failure must not corrupt invoice data)
- reusable sending service design for future payroll emails

Hard constraints:
- do not implement reminder schedules, Celery, payroll email, Stripe, or dashboards
- do not add later-phase features
- keep changes incremental and maintainable

Acceptance checks (must all pass):
- invoice can be emailed
- email content is correct
- delivery attempt is logged
- send failures do not corrupt invoice data
- sent status works as intended

After completion, report:
- decision matrix table and chosen option
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P06` - Phase 6 Payroll MVP
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 6 only.

Build the payroll MVP workflow.

Focus only on:
- employee management support if needed
- payroll batch upload page
- CSV and Excel acceptance
- payroll file parsing
- row validation
- invalid row reporting
- saving valid rows safely
- payroll batch history
- payslip record creation
- employee self-service payslip view
- secure access restrictions

Requirements:
- HR and admin roles should control payroll upload
- employees should only see their own payslips
- validation must be clear and not silent
- do not build payslip PDF, Stripe, Celery, reminders, or advanced reporting yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P07` - Phase 7 Payroll PDF Output
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 7 only.

Add payslip PDF generation.

Focus only on:
- payslip PDF template
- employee details
- payroll period details
- earnings section
- deductions section
- net pay section
- professional layout and styling
- strict value accuracy

Requirements:
- payslip PDF must match stored payroll data exactly
- output must be clean, readable, and professional
- access must remain properly restricted
- do not build payroll email sending, reminders, Stripe, or dashboard reporting yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P08` - Phase 8 Bulk Import Framework
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 8 only.

Create a reusable bulk import framework for invoices and payroll.

Focus only on:
- parsing layer separation
- validation layer separation
- save layer separation
- import job tracking
- import row error tracking
- downloadable error reporting
- import summary reporting

Requirements:
- support reusable patterns for invoice and payroll imports
- do not silently ignore invalid rows
- make row-level errors clear
- keep code modular and maintainable
- do not build reminder jobs, Stripe changes, or dashboard features yet

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P09` - Phase 9 Background Jobs and Reminders
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 9 only.

Add background job support and overdue reminders.

Focus only on:
- Celery configuration
- Redis configuration
- background email sending where appropriate
- overdue reminder task
- retry-safe task handling
- duplicate reminder prevention
- useful task logging

Requirements:
- async jobs must not duplicate sends accidentally
- failures must be visible and manageable
- keep implementation clean and not overcomplicated
- do not work on Stripe changes or unrelated UI work in this phase

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P10` - Phase 10 Stripe Payments
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 10 only.

Add Stripe payment support for invoices.

Focus only on:
- payment start flow
- linking Stripe payment session to invoice
- success and cancel flow
- secure webhook endpoint
- webhook signature verification
- invoice payment status update from webhook
- webhook event logging
- duplicate webhook safety

Requirements:
- successful payments must update invoices correctly
- cancelled or failed flows must not corrupt invoice state
- webhook handling must be idempotent where needed
- do not add unrelated dashboard or payroll changes in this phase

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P11` - Phase 11 Reporting Dashboard
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 11 only.

Build the operational dashboard.

Focus only on:
- dashboard homepage
- invoice counts by status
- payment summary
- payroll summary
- recent uploads
- recent email activity
- recent system activity
- simple charts only where useful

Requirements:
- accuracy is more important than visual complexity
- page should remain clean, fast, and understandable
- avoid unnecessary chart clutter
- do not change core workflow logic unless absolutely needed for reporting accuracy

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `P12` - Phase 12 Hardening and Cleanup
```text
Read AGENT.md and TASK.md and follow them strictly.

Work on Phase 12 only.

Do final hardening and cleanup.

Focus only on:
- permission review
- validation review
- model constraint review
- error handling review
- logging review
- dead code cleanup
- naming consistency cleanup
- setup documentation
- environment variable documentation
- background job setup documentation
- Stripe webhook setup documentation
- README update

Requirements:
- improve maintainability without rewriting stable working features
- remove unnecessary complexity where found
- keep final handover clear for another developer
- do not introduce new major features in this phase

After completion, report:
- files created
- files updated
- what now works
- what remains next
- risks or assumptions
```

### `VERIFY` - Verification Prompt (After Any Phase)
```text
Read AGENT.md and TASK.md and follow them strictly.

Review the work completed for the current phase only.

Check for:
- broken imports
- inconsistent naming
- obvious logic issues
- missing permission checks
- missing validation
- migration issues
- maintainability concerns
- duplicated logic
- incomplete acceptance criteria

Do not add new features.
Only fix issues directly related to the current phase.

After completion, report:
- issues found
- fixes made
- files updated
- remaining risks or assumptions
```

### `REFACTOR` - Safe Improvement Only
```text
Read AGENT.md and TASK.md and follow them strictly.

Refactor only the current phase implementation.

Goals:
- improve readability
- improve maintainability
- reduce duplication
- preserve existing behavior
- keep structure clean for future phases

Rules:
- do not change business behavior
- do not start later-phase work
- do not rewrite unrelated modules
- make small safe improvements only

After completion, report:
- what was refactored
- why it was refactored
- files updated
- confirmation that behavior was preserved
- any remaining risks
```

### `BUGFIX` - Current Phase Only
```text
Read AGENT.md and TASK.md and follow them strictly.

Fix bugs only in the current phase implementation.

Requirements:
- identify root cause first
- make minimal safe changes
- do not introduce later-phase features
- do not rewrite unrelated code
- preserve maintainability

After completion, report:
- bugs fixed
- root cause
- files updated
- what now works
- remaining risks or assumptions
```

### `HANDOVER` - Final Pre-Handover Prompt
```text
Read AGENT.md and TASK.md and follow them strictly.

Perform a final project review before handover.

Check:
- project structure
- role protection
- invoice workflow
- payroll workflow
- imports
- PDF and Excel output
- email handling
- reminders
- payments
- dashboard accuracy
- logging
- setup documentation

Goals:
- identify gaps
- identify fragile areas
- identify anything incomplete against AGENT.md and TASK.md
- suggest only high-value fixes

Do not do a massive rewrite.
Prefer small targeted fixes.

After completion, report:
- completed areas
- incomplete areas
- risky areas
- suggested final fixes in priority order
```
