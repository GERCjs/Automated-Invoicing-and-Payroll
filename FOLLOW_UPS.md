# Follow-Up List

## Phase 5 Email Sending Follow-Ups

1. Add provider webhook ingestion for final email events.
Track `delivered`, `bounced`, `complained`, and `suppressed` from Resend webhooks and persist them against `EmailDeliveryLog`.

2. Extend `EmailDeliveryLog` status model or metadata mapping.
Represent both transport success and final delivery outcome so `sent` is not the only terminal state in app reporting.

3. Add duplicate-send guard on invoice send action.
Prevent accidental rapid repeat sends with UI disable and server-side cooldown or idempotency key per invoice/time window.

4. Add explicit resend flow.
Provide a deliberate `Resend invoice` action with reason tracking and an audit entry, instead of repeated normal send clicks.

5. Add webhook signature verification and replay protection.
Validate incoming webhook signatures and reject duplicate or replayed events for integrity.

6. Add operational alerts for failed or risky events.
Surface failed, suppressed, and complained events in admin or finance views so users can act quickly.

7. Add reconciliation checks between app logs and provider events.
Run periodic checks to detect mismatches between local `EmailDeliveryLog` data and Resend event history.
