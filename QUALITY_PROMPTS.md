# Quality Prompt Library

## Purpose

Use these exact prompts when you want the coding agent to keep the system clean, reliable, maintainable, and scalable while prioritizing correctness, auditability, and business workflow usability over unnecessary complexity.

These prompts are designed to work with:

- `AGENT.md`
- `TASK.md`
- `QA_GATE.md`
- `PROMPTS.md`

## How To Use

1. Pick the prompt that matches the work type.
2. Copy the full prompt block.
3. Replace placeholders such as `<PHASE_NUMBER>`, `<PHASE_NAME>`, or `<TASK_DESCRIPTION>`.
4. Send it to the coding agent.

## Global Quality Prompt

Use this at the start of any major work session.

```text
You are working in this repository.

Read and follow these files in this exact order:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Primary goal:
Keep the system clean, reliable, maintainable, and scalable.
Prioritize correctness, auditability, data integrity, security, and business workflow usability over unnecessary complexity.

Required working rules:
- Work in small, safe, incremental steps.
- Do not rewrite unrelated code.
- Do not jump ahead to later phases.
- Do not add unnecessary packages or infrastructure.
- Preserve existing working features.
- Keep views thin and move business logic into services where appropriate.
- Validate all user input.
- Protect role-restricted and sensitive workflows.
- Log important business actions for auditability.
- Use transactions for critical writes.
- Never silently save invalid uploaded rows.
- Keep UI business-like, clear, and usable.
- Avoid clever shortcuts that make handover harder.

Before changing code:
- Inspect the relevant files first.
- Explain the plan.
- Identify risks, assumptions, and acceptance criteria.

After changing code:
- Run relevant checks or tests where practical.
- Report exactly what changed, why, what is complete, what remains, and any risks.
```

## Phase Execution Prompt

Use this when starting any approved phase.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Work on Phase <PHASE_NUMBER> only: <PHASE_NAME>.

Scope:
- <SCOPE_ITEM_1>
- <SCOPE_ITEM_2>
- <SCOPE_ITEM_3>

Quality priorities, in order:
1. correctness
2. security
3. data integrity
4. auditability
5. maintainability
6. business workflow usability
7. performance
8. visual polish

Hard constraints:
- Do not start later phase work.
- Do not rewrite unrelated modules.
- Do not introduce unnecessary dependencies.
- Do not break completed phases.
- Keep changes focused and incremental.
- Keep business logic out of templates.
- Keep complex logic out of views where a service layer is more appropriate.
- Validate input and handle failures safely.
- Add audit logs for important business actions.

Acceptance criteria:
- Match the Phase <PHASE_NUMBER> acceptance criteria in TASK.md.
- Match the relevant prompt in PROMPTS.md.
- Preserve all earlier completed workflows.
- Keep the implementation understandable for another developer.

Before implementation:
- Summarize the files you will inspect.
- Summarize the planned changes.
- State risks or assumptions.

After implementation:
Return this exact report format:
1. What was changed
2. Why it was changed
3. What is complete
4. What still remains
5. Risks or assumptions
6. Checks or tests run
```

## Approval-Gated Planning Prompt

Use this when you want the agent to observe and plan only.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Mode: observe and plan only.
Do not edit files.
Do not run migrations.
Do not implement code.
Do not change configuration.

Task to analyze:
<TASK_DESCRIPTION>

Focus your analysis on:
- what already exists
- what is missing
- what is risky
- what should be improved
- what phase the work belongs to
- whether TASK.md allows this work now
- what should require approval before implementation

Reasoning priorities:
- correctness
- auditability
- data integrity
- security
- maintainability
- business workflow usability
- avoiding unnecessary complexity

Return this exact report format:
1. Files reviewed
2. Existing implementation
3. Gaps or risks
4. Recommended phased plan
5. Approval gates
6. Suggested next prompt to execute
```

## QA Gate Prompt

Use this after completing any phase.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Run the QA gate for Phase <PHASE_NUMBER>: <PHASE_NAME>.

Review only the current phase scope.
Do not add new features.
Do not move ahead to future phase work.
Fix only small, necessary defects directly related to the current phase.

Check for:
- broken imports
- syntax or structural issues
- migration issues
- model consistency
- route or page breakage
- missing permission checks
- missing validation
- missing audit logs for important actions
- obvious regressions from earlier completed phases
- duplicated logic that creates maintenance risk
- acceptance criteria gaps for the current phase only

Quality priorities:
- correctness first
- security and data integrity next
- maintainability before visual polish
- business workflow clarity over complexity

Return this exact QA_GATE.md format:
1. Phase reviewed
2. Files checked
3. Issues found
4. Fixes made
5. Remaining risks
6. Approval decision
7. Next recommendation

Approval decision must be exactly one of:
- PASS
- PASS WITH MINOR RISKS
- FAIL
```

## Safe Bugfix Prompt

Use this when something in the current phase is broken.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Fix this bug only:
<BUG_DESCRIPTION>

Current phase:
Phase <PHASE_NUMBER>: <PHASE_NAME>

Rules:
- Identify the root cause before changing code.
- Make the smallest safe fix.
- Do not rewrite unrelated code.
- Do not add later-phase features.
- Preserve existing behavior outside the bug.
- Add or update focused tests if the bug has meaningful regression risk.
- Keep auditability, validation, and permissions intact.

Return this exact report format:
1. Root cause
2. Files changed
3. Fix made
4. Why the fix is safe
5. Checks or tests run
6. Remaining risks
```

## Safe Refactor Prompt

Use this when code works but needs cleanup.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Refactor only this current-phase area:
<REFACTOR_AREA>

Current phase:
Phase <PHASE_NUMBER>: <PHASE_NAME>

Goals:
- improve readability
- improve maintainability
- reduce meaningful duplication
- keep business logic in the right layer
- preserve existing behavior
- keep future handover easier

Rules:
- Do not change business behavior unless explicitly approved.
- Do not start later-phase work.
- Do not rewrite unrelated modules.
- Do not introduce unnecessary abstractions.
- Do not add new dependencies unless clearly justified.
- Keep changes small and reviewable.

Return this exact report format:
1. What was refactored
2. Why it was refactored
3. Files changed
4. Behavior preserved
5. Checks or tests run
6. Remaining risks
```

## Payment Improvement Prompt

Use this only after payment work is approved.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md
5) PAYMENT_RECOMMENDED_PLAN.md

Work only on approved payment phase:
<PAYMENT_PHASE_KEY>: <PAYMENT_PHASE_NAME>

Approved scope:
- <SCOPE_ITEM_1>
- <SCOPE_ITEM_2>
- <SCOPE_ITEM_3>

Payment quality rules:
- Payment state changes must be correct and auditable.
- Webhook handling must be signature-verified and idempotent.
- Duplicate payment events must not corrupt invoice state.
- Failed, cancelled, or incomplete flows must not mark invoices paid.
- Customer-facing payment UI must be clear and business-friendly.
- Stripe configuration errors must fail safely.
- Do not add surcharge, refund, reporting, or future payment features unless they are part of this approved phase.

Return this exact report format:
1. What was changed
2. Why it was changed
3. What is complete
4. What still remains
5. Risks or assumptions
6. Checks or tests run
7. Whether the next payment phase is safe to start
```

## Pre-Handover Review Prompt

Use this near the end of the project or before submission.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md

Perform a pre-handover review.

Do not do a major rewrite.
Do not add new features unless they are small fixes needed for correctness or safety.

Review:
- project structure
- authentication and roles
- permission checks
- audit logging
- invoicing workflow
- payroll workflow
- imports and validation
- PDF and Excel output
- email sending
- reminders or background tasks if implemented
- Stripe payments if implemented
- dashboard/reporting if implemented
- setup documentation
- environment variable documentation
- obvious secrets or unsafe defaults

Prioritize findings by:
1. correctness
2. security
3. data integrity
4. auditability
5. maintainability
6. business workflow usability

Return this exact report format:
1. Completed areas
2. Incomplete areas
3. Risks by priority
4. Recommended fixes
5. Files that should be updated
6. Submission readiness decision
```

## Exact Next Prompt For Payment Planning

Use this if the next work is still payment planning only.

```text
You are working in this repository.

Read and follow:
1) AGENT.md
2) TASK.md
3) QA_GATE.md
4) PROMPTS.md
5) PAYMENT_RECOMMENDED_PLAN.md

Mode: observe and plan only.
Do not edit files.
Do not implement code.

Review the current payment implementation against PAYMENT_RECOMMENDED_PLAN.md.

Focus only on:
- whether P0 scope alignment is satisfied
- whether P1 Payment UX Clarity is ready to implement
- whether P2 Stripe Configuration Guardrails has any hidden risks
- whether payment work conflicts with TASK.md active phase

Return:
1. Files reviewed
2. Current payment readiness
3. Risks before implementation
4. Recommended first approved implementation phase
5. Exact implementation prompt to use after approval
```

