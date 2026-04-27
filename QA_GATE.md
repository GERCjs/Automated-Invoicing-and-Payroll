# QA_GATE.md

## Purpose
Run this QA gate after each completed phase.

Its role is to confirm whether the current phase is stable enough to proceed to the next phase.

This gate focuses on:
- obvious breakages
- missing protections
- missing validation
- migration issues
- regressions

This is a phase-based quality checkpoint, not a full testing framework.

---

## Core QA Rules
- Review only the current phase scope.
- Do not add new features.
- Do not move ahead to future phase work.
- Prefer small, safe fixes.
- Identify root cause before changing code.
- Preserve maintainability.
- Report assumptions clearly.

---

## What the QA Gate Must Check Every Time
Check for:
- broken imports
- syntax or structural issues
- migration issues
- model consistency
- route or page breakage
- missing permission checks
- missing validation
- obvious regressions from earlier completed phases
- acceptance criteria of the current phase only
- duplicated logic when it creates maintenance risk

---

## Phase-Specific Review Rule
For each gate, compare completed work against:
- `AGENT.md`
- `TASK.md`
- the current phase prompt in `PROMPTS.md`

Confirm and report:
- what was required
- what was completed
- what is missing
- what is risky
- whether the phase is safe to approve

---

## Output Format
Always return these sections:
1. Phase reviewed
2. Files checked
3. Issues found
4. Fixes made
5. Remaining risks
6. Approval decision
7. Next recommendation

---

## Approval Decision Format
Use only:
- `PASS`
- `PASS WITH MINOR RISKS`
- `FAIL`

Decision meaning:
- `PASS`: phase is stable enough to continue.
- `PASS WITH MINOR RISKS`: phase can continue, but non-blocking concerns exist.
- `FAIL`: phase must not continue until blocking issues are fixed.

---

## Minimum Fix Scope
If issues are found, fix only what is necessary for the current phase:
- make minimal, safe fixes related to current-phase defects
- do not refactor unrelated modules
- do not introduce future-phase work

---

## Suggested Review Checklist
- project still runs
- migrations apply cleanly
- permissions still make sense
- validation exists where required
- status logic still behaves correctly
- templates or pages referenced by the phase exist
- no obvious data integrity issue introduced
- acceptance criteria are met

---

## Final Instruction
This QA gate is a release checkpoint between phases.

It is not a feature-building agent.
