# Automated Invoicing and Payroll - Prompt Guide

## What this is
This repository is set up to run a coding agent with structured, phase-based prompts.

The goal is to keep work safe, focused, and sequential (foundation first, then models, then workflows, etc.) for an Automated Invoicing and Payroll system.

## Core files
- `AGENT.md`: Project rules, architecture direction, scope, and build order.
- `TASK.md`: Task requirements for the implementation.
- `PROMPTS.md`: Prompt library and dispatcher pattern you send to the agent.
- `QA_GATE.md`: Phase-based quality gate to review the current completed phase before moving forward.
- `DATABASE_SETUP_NOTES.md`: Shared database setup, migration commands, and ERD table mapping notes.

## Current test workflow
Use the dedicated Django test settings for routine verification. They force an isolated SQLite test database, keep production WhiteNoise manifest storage out of test runs, and prevent automated tests from sending real emails.

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
```

Focused suites used during hardening:

```powershell
.\.venv\Scripts\python.exe manage.py test accounts invoicing payments --settings=config.test_settings --verbosity 1
.\.venv\Scripts\python.exe manage.py test payroll reports notifications imports core --settings=config.test_settings --verbosity 1
```

For MySQL test runs, use a dedicated disposable test database, an approved cleanup policy, or `--keepdb` when appropriate. Do not let Django delete or reuse an unknown shared test database interactively.

## Bank transfer configuration
Bank-transfer payment instructions are shown on payable invoice pages when invoices are pending/viewed/overdue. Configure these optional environment variables in local `.env` and Render:

```env
BANK_TRANSFER_ACCOUNT_NAME=Your Company Name
BANK_TRANSFER_BANK_NAME=DBS
BANK_TRANSFER_ACCOUNT_NUMBER=001-234567-8
BANK_TRANSFER_PAYNOW_ID=Your PayNow ID
BANK_TRANSFER_BIC=DBSSSGSG
BANK_TRANSFER_INSTRUCTIONS=Enter the payment reference in your bank transfer comments/reference field.
```

Each payable invoice gets one stable manual payment reference. Admin/Finance users confirm the transfer after matching that reference in the banking app.

## Payment reminder scheduling
Payment reminder settings control which invoices are eligible for before-due, due-date, after-due, and repeat-overdue reminder emails. The website also provides an Admin Console button to send currently due reminders manually.

For automatic daily reminders after deployment, configure the hosting platform scheduler, cron, Windows Task Scheduler, or equivalent to run:

```bash
python manage.py send_payment_reminders --base-url https://client-domain.com
```

The scheduler should run once per day. The repeat-overdue setting uses the last sent repeat reminder date, so invoices are skipped until they are due for another reminder.

## How to use (exact steps)
1. Open `PROMPTS.md`.
2. Choose one prompt key from the key map:
   - `P01` to `P12`
   - `VERIFY`
   - `REFACTOR`
   - `BUGFIX`
   - `HANDOVER`
3. Copy the **Dispatcher Prompt** block from `PROMPTS.md`.
4. Replace:
   - `PROMPT_KEY: <PUT_KEY_HERE>`
   with your selected key, for example:
   - `PROMPT_KEY: P03`
5. Send that full dispatcher prompt to your coding agent.
6. The agent should execute only that selected prompt and return:
   1. What was changed
   2. Why it was changed
   3. What is complete
   4. What still remains
   5. Risks or assumptions

## Example usage
Use this in your message to the agent:

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) PROMPTS.md

PROMPT_KEY: P01

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

## How to use QA Gate (after each phase)
Run QA Gate after a phase is completed and before starting the next phase.

1. Complete a phase using one prompt key (for example `P03`).
2. Ask the agent to run the QA gate using `QA_GATE.md`.
3. Ensure the QA gate reviews only the current phase against:
   - `AGENT.md`
   - `TASK.md`
   - the current phase prompt in `PROMPTS.md`
4. Read the approval decision:
   - `PASS`: continue to next phase
   - `PASS WITH MINOR RISKS`: continue, but track listed risks
   - `FAIL`: fix blocking issues first, then run QA gate again

### QA gate message example
```text
Run the QA gate for the current completed phase only.

Follow:
1) QA_GATE.md
2) AGENT.md
3) TASK.md
4) PROMPTS.md

Do not add new features.
Do not review future phases.
Apply only minimal safe fixes if required.

Return exactly:
1. Phase reviewed
2. Files checked
3. Issues found
4. Fixes made
5. Remaining risks
6. Approval decision
7. Next recommendation
```

## Prompt key guide
- `P01`: Foundation setup (DONE)
- `P02`: Core data models (DONE)
- `P03`: Invoicing MVP
- `P04`: Invoice PDF and Excel output
- `P05`: Invoice email sending
- `P06`: Payroll MVP
- `P07`: Payroll PDF output
- `P08`: Bulk import framework
- `P09`: Background jobs and reminders
- `P10`: Stripe payments
- `P11`: Reporting dashboard
- `P12`: Hardening and cleanup
- `VERIFY`: Validate current phase and fix phase-related issues only
- `REFACTOR`: Safe refactor of current phase only
- `BUGFIX`: Bug fixing for current phase only
- `HANDOVER`: Final review before handover

## Tips
- Run one phase key at a time (`P01`, then `P02`, etc.).
- Avoid jumping ahead to later phases until earlier phases are stable.
- Use `VERIFY` after each major phase to catch quality issues early.
- Use `QA_GATE.md` as the release checkpoint between phases.
- Use `REFACTOR` only when behavior should stay the same.
- Use `BUGFIX` when you already know the current phase has defects.
