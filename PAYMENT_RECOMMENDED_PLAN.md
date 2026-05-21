# Payment Recommended Plan

## Purpose

This document plans the payment work for the Automated Invoicing and Payroll System.
It focuses on Stripe Checkout with cards and PayNow for invoice payments.

No payment implementation phase should begin until the previous phase is complete or the project owner explicitly approves moving ahead.

## Current Payment Scope

The selected payment approach is:

- Stripe Checkout for hosted payment collection.
- Card payments through Stripe.
- PayNow payments through Stripe for Singapore/SGD invoice payments.
- Invoice payment status updates through Stripe webhook handling.
- Payment auditability through payment records, webhook event records, and audit logs.

## Current Implementation Observed

The project already includes:

- A `payments` Django app.
- `PaymentRecord` model for payment attempts and statuses.
- `StripeWebhookEvent` model for webhook processing history.
- Public invoice checkout start flow.
- Logged-in customer invoice checkout start flow.
- Stripe Checkout session creation.
- `card` and `paynow` configured as Checkout payment method types.
- Stripe webhook endpoint.
- Stripe webhook signature verification.
- Duplicate webhook event handling.
- Successful payment handling that updates invoice status to paid.
- Basic success and cancel pages.
- Tests for checkout start, webhook success, duplicate webhook events, retry behavior, invalid signatures, and checkout result pages.

## Approval Rule

Each phase has one of two ways to proceed:

- Proceed after the phase is completed and verified.
- Proceed earlier only if the project owner explicitly approves.

If a phase reveals a security, data integrity, or workflow risk, pause and review before moving to the next phase.

## Phase P0: Align Project Scope

### Goal

Confirm that payment work is approved even though `TASK.md` currently lists Phase 1 as the active phase and Stripe work as a later phase.

### Work Items

- Confirm whether payment planning can continue ahead of the active task file.
- Decide whether to update `TASK.md` later to reflect payment work as the active approved scope.
- Keep this phase documentation-only unless an update is approved.

### Completion Criteria

- Project owner confirms payment work may continue.
- Scope is clear: research only, planning only, or implementation.

### Proceed Gate

Proceed to Phase P1 only after project owner approval.

## Phase P1: Payment UX Clarity

### Goal

Make the user-facing payment flow accurately reflect that both cards and PayNow are supported.

### Reasoning

The current invoice buttons say `Pay with Card`, but the Stripe Checkout session is configured with both `card` and `paynow`.
This can confuse customers and understate the payment options.

### Work Items

- Rename public invoice button to `Pay by Card or PayNow`.
- Rename customer invoice button to `Pay by Card or PayNow`.
- Improve success page copy so customers understand payment may be confirmed by Stripe webhook.
- Improve cancel page copy so customers understand no payment was completed.

### Completion Criteria

- Public invoice payment action clearly mentions Card and PayNow.
- Customer invoice payment action clearly mentions Card and PayNow.
- Success/cancel pages are clear and business-friendly.
- No payment logic changes are introduced in this phase.

### Proceed Gate

Proceed to Phase P2 after this phase is reviewed and approved.

## Phase P2: Stripe Configuration Guardrails

### Goal

Prevent unclear failures when Stripe settings or invoice conditions are invalid.

### Reasoning

Payment errors should be explainable to business users and developers.
Stripe configuration issues should fail safely before a customer reaches a broken checkout flow.

### Work Items

- Validate `STRIPE_SECRET_KEY` before creating checkout sessions.
- Validate `STRIPE_WEBHOOK_SECRET` for webhook processing.
- Add clearer error messages for missing or invalid Stripe setup.
- Confirm invoice currency before enabling PayNow.
- Treat PayNow as available only for SGD invoices.

### Completion Criteria

- Missing Stripe key produces a clear internal error path.
- Missing webhook secret produces a clear webhook error path.
- Non-SGD invoices do not attempt to offer PayNow.
- Existing checkout tests still pass.

### Proceed Gate

Proceed to Phase P3 after this phase is verified and approved.

## Phase P3: Payment State Safety

### Goal

Reduce duplicate and confusing payment records for the same unpaid invoice.

### Reasoning

Currently, repeated checkout starts can create multiple pending payment records for one invoice.
This is not always wrong, but it can complicate reconciliation and support.

### Work Items

- Decide whether to reuse an existing pending checkout session or create a new one each time.
- If reusing, locate the latest pending Stripe payment record for the invoice.
- If creating a new session, expire or clearly supersede older pending sessions where possible.
- Add tests for repeated checkout starts.

### Completion Criteria

- The system has a clear rule for repeat payment attempts.
- Payment records remain understandable for finance users.
- Repeated clicks do not create unnecessary confusion.
- Existing successful payment and webhook behavior remains unchanged.

### Proceed Gate

Proceed to Phase P4 after this phase is reviewed and approved.

## Phase P4: Webhook-First Production Confirmation

### Goal

Make Stripe webhooks the trusted source of payment confirmation.

### Reasoning

The current success redirect includes a sandbox fallback that can mark payments as succeeded after returning from Stripe.
That is convenient for local testing, but production payment confirmation should rely on verified webhook events.

### Work Items

- Keep redirect success page as a customer-facing status page.
- Avoid treating redirect alone as authoritative in production.
- Make sandbox fallback explicitly controlled by a setting if still needed.
- Ensure webhook processing remains idempotent.
- Add tests for redirect behavior with and without sandbox fallback.

### Completion Criteria

- Production confirmation depends on verified Stripe webhook events.
- Sandbox fallback is clearly labeled and controlled.
- Duplicate webhook events still do not corrupt state.
- Failed webhook events remain retryable.

### Proceed Gate

Proceed to Phase P5 after this phase is verified and approved.

## Phase P5: PayNow-Specific Rules

### Goal

Handle PayNow as a first-class local payment method rather than just a hidden Stripe option.

### Reasoning

PayNow has different business behavior from cards.
It is Singapore/SGD-focused, customer-initiated, QR-based, single-use, and does not use chargeback/dispute flow like cards.

### Work Items

- Document PayNow rules in project setup or payment docs.
- Add UI copy where appropriate to explain that PayNow uses a QR flow.
- Store or expose payment method type when Stripe provides it.
- Ensure reporting can distinguish card payments from PayNow payments later.

### Completion Criteria

- PayNow behavior is documented for developers and business users.
- Finance users can eventually identify whether payment was card or PayNow.
- PayNow limitations are not hidden from the project owner.

### Proceed Gate

Proceed to Phase P6 after this phase is reviewed and approved.

## Phase P6: Fees and Reconciliation

### Goal

Support business-owner payment reporting: gross paid, Stripe fees, net received, and who bears the cost.

### Reasoning

Stripe fees are normally borne by the merchant unless the business adds a compliant surcharge or fee recovery process.
The current system records invoice total and payment status, but not Stripe fee or net settlement.

### Work Items

- Decide whether the business absorbs Stripe fees or passes a compliant surcharge to customers.
- Add fields if approved:
  - payment method type
  - gross amount
  - processing fee
  - net amount
  - fee bearer
- Consider using Stripe balance transaction data for accurate fees.
- Add finance-facing payment summary later.

### Completion Criteria

- Business owner can understand actual received amount.
- Payment records can support reconciliation.
- Fee-bearing policy is explicit.
- No surcharge behavior is added unless legally and project-owner approved.

### Proceed Gate

Proceed to Phase P7 after this phase is reviewed and approved.

## Phase P7: Refund Handling

### Goal

Add safe refund support after payment capture is stable.

### Reasoning

The model already has a `refunded` status, but there is no complete refund workflow yet.
Refunds should be controlled, logged, and confirmed through Stripe.

### Work Items

- Add admin or finance-only refund action.
- Create refund through Stripe.
- Store refund reference and status.
- Listen for refund webhook events where appropriate.
- Support partial refund only if approved.

### Completion Criteria

- Refunds are role-protected.
- Refund attempts are logged.
- Payment and invoice state remain consistent.
- Partial refund behavior is clearly defined before implementation.

### Proceed Gate

Proceed to Phase P8 after this phase is reviewed and approved.

## Phase P8: Payment Reporting

### Goal

Make payment activity visible to finance users.

### Reasoning

Payment tracking is useful only if finance users can review paid, pending, failed, cancelled, and refunded payments clearly.

### Work Items

- Add a finance payment list page.
- Add filters by status, provider, date, invoice number, customer, and payment method.
- Add payment summary widgets.
- Add recent webhook event visibility for support/debugging.
- Include payment totals in the dashboard if approved.

### Completion Criteria

- Finance users can review payment activity without using Django admin.
- Failed and pending payments are visible.
- Payment totals are understandable.
- Reporting does not expose sensitive Stripe payload data unnecessarily.

### Proceed Gate

Proceed to Phase P9 after this phase is reviewed and approved.

## Phase P9: Security and Documentation Hardening

### Goal

Prepare the payment flow for handover and safer deployment.

### Reasoning

Payment code handles money and customer data.
It needs clear setup instructions, safer environment examples, and operational notes.

### Work Items

- Remove real-looking secrets from `.env.example`.
- Rotate any credentials that were ever valid and committed.
- Document Stripe dashboard setup.
- Document webhook endpoint setup.
- Document local Stripe CLI testing.
- Document PayNow activation requirements.
- Document expected webhook events.

### Completion Criteria

- No real credentials remain in example files.
- Stripe setup is reproducible by another developer.
- Webhook testing is documented.
- Production risks and assumptions are written down.

### Proceed Gate

This phase completes the payment hardening plan.

## Recommended Starting Point

Start with:

1. Phase P0: Align Project Scope.
2. Phase P1: Payment UX Clarity.
3. Phase P2: Stripe Configuration Guardrails.
4. Phase P3: Payment State Safety.
5. Phase P4: Webhook-First Production Confirmation.

These phases improve correctness and clarity without adding major new payment features.

## Current Approval Status

| Phase | Status |
| --- | --- |
| P0: Align Project Scope | Pending approval |
| P1: Payment UX Clarity | Not started |
| P2: Stripe Configuration Guardrails | Not started |
| P3: Payment State Safety | Not started |
| P4: Webhook-First Production Confirmation | Not started |
| P5: PayNow-Specific Rules | Not started |
| P6: Fees and Reconciliation | Not started |
| P7: Refund Handling | Not started |
| P8: Payment Reporting | Not started |
| P9: Security and Documentation Hardening | Not started |

