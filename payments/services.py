from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.audit import log_event
from invoicing.models import Invoice

from .models import PaymentRecord, StripeWebhookEvent

WEBHOOK_EVENT_COMPLETED = "checkout.session.completed"
WEBHOOK_EVENT_ASYNC_SUCCEEDED = "checkout.session.async_payment_succeeded"
WEBHOOK_EVENT_ASYNC_FAILED = "checkout.session.async_payment_failed"
WEBHOOK_EVENT_EXPIRED = "checkout.session.expired"
WEBHOOK_EVENT_REFUND_CREATED = "refund.created"
WEBHOOK_EVENT_REFUND_UPDATED = "refund.updated"
WEBHOOK_EVENT_REFUND_FAILED = "refund.failed"
PAYNOW_ENABLED_CURRENCY = "SGD"

SUPPORTED_WEBHOOK_EVENTS = {
    WEBHOOK_EVENT_COMPLETED,
    WEBHOOK_EVENT_ASYNC_SUCCEEDED,
    WEBHOOK_EVENT_ASYNC_FAILED,
    WEBHOOK_EVENT_EXPIRED,
    WEBHOOK_EVENT_REFUND_CREATED,
    WEBHOOK_EVENT_REFUND_UPDATED,
    WEBHOOK_EVENT_REFUND_FAILED,
}


def _import_stripe():
    try:
        import stripe  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise ImproperlyConfigured(
            "Stripe package is required. Install dependencies from requirements.txt."
        ) from exc
    return stripe


def _to_minor_units(amount: Decimal) -> int:
    normalized = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((normalized * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _require_stripe_secret_key() -> str:
    secret_key = (settings.STRIPE_SECRET_KEY or "").strip()
    if not secret_key:
        raise ImproperlyConfigured(
            "STRIPE_SECRET_KEY is missing. Configure Stripe checkout before accepting payments."
        )
    return secret_key


def _require_stripe_webhook_secret() -> str:
    webhook_secret = (settings.STRIPE_WEBHOOK_SECRET or "").strip()
    if not webhook_secret:
        raise ImproperlyConfigured(
            "STRIPE_WEBHOOK_SECRET is missing. Configure Stripe webhooks before processing events."
        )
    return webhook_secret


def _normalize_currency_code(raw_currency: str | None) -> str:
    currency_code = (raw_currency or PAYNOW_ENABLED_CURRENCY).strip().upper()
    if len(currency_code) != 3 or not currency_code.isalpha():
        raise ValueError("Invoice currency is invalid for Stripe checkout.")
    return currency_code


def _resolve_checkout_payment_method_types(currency_code: str) -> list[str]:
    payment_method_types = ["card"]
    if currency_code == PAYNOW_ENABLED_CURRENCY:
        payment_method_types.append("paynow")
    return payment_method_types


def _normalize_external_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal, uuid.UUID)):
        return str(value)
    return ""


def _append_checkout_cancel_reference(cancel_url: str, payment_record: PaymentRecord) -> str:
    separator = "&" if "?" in cancel_url else "?"
    return f"{cancel_url}{separator}{urlencode({'payment_reference': payment_record.payment_reference})}"


def _resolve_refunded_invoice_status(invoice: Invoice) -> str:
    return Invoice.STATUS_REFUNDED


def _apply_local_refunded_state(payment_record: PaymentRecord) -> None:
    with transaction.atomic():
        locked_record = (
            PaymentRecord.objects.select_related("invoice")
            .select_for_update()
            .get(pk=payment_record.pk)
        )
        invoice = locked_record.invoice
        updates = []
        if locked_record.status != PaymentRecord.STATUS_REFUNDED:
            locked_record.status = PaymentRecord.STATUS_REFUNDED
            updates.append("status")
        if updates:
            updates.append("updated_at")
            locked_record.save(update_fields=updates)
        if invoice.status == Invoice.STATUS_PAID:
            invoice.status = _resolve_refunded_invoice_status(invoice)
            invoice.save(update_fields=["status", "updated_at"])


def _build_checkout_session(
    *,
    invoice: Invoice,
    payment_record: PaymentRecord,
    success_url: str,
    cancel_url: str,
) -> Any:
    stripe = _import_stripe()
    stripe.api_key = _require_stripe_secret_key()
    currency_code = _normalize_currency_code(invoice.currency)
    payment_method_types = _resolve_checkout_payment_method_types(currency_code)
    return stripe.checkout.Session.create(
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        payment_method_types=payment_method_types,
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency_code.lower(),
                    "unit_amount": _to_minor_units(invoice.total_amount),
                    "product_data": {
                        "name": f"Invoice {invoice.invoice_number}",
                        "description": f"Payment for {invoice.customer.name}",
                    },
                },
            }
        ],
        metadata={
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "payment_record_id": str(payment_record.id),
            "payment_reference": payment_record.payment_reference,
        },
        client_reference_id=invoice.invoice_number,
    )


def create_checkout_for_invoice(
    *,
    invoice: Invoice,
    success_url: str,
    cancel_url: str,
    initiated_by=None,
) -> PaymentRecord:
    if invoice.status == Invoice.STATUS_PAID:
        raise ValueError("Invoice is already paid.")
    _require_stripe_secret_key()
    normalized_currency = _normalize_currency_code(invoice.currency)

    payment_record = PaymentRecord.objects.create(
        invoice=invoice,
        payment_reference=f"PAY-{uuid.uuid4().hex[:12].upper()}",
        provider=PaymentRecord.PROVIDER_STRIPE,
        status=PaymentRecord.STATUS_PENDING,
        amount=invoice.total_amount,
        currency=normalized_currency,
        created_by=initiated_by,
    )

    session = _build_checkout_session(
        invoice=invoice,
        payment_record=payment_record,
        success_url=success_url,
        cancel_url=_append_checkout_cancel_reference(cancel_url, payment_record),
    )
    payment_record.stripe_checkout_session_id = session.id
    payment_record.external_transaction_id = _normalize_external_id(
        getattr(session, "payment_intent", None)
    )
    payment_record.save(
        update_fields=[
            "stripe_checkout_session_id",
            "external_transaction_id",
            "updated_at",
        ]
    )
    return payment_record


def retrieve_checkout_session(session_id: str) -> Any:
    stripe = _import_stripe()
    stripe.api_key = _require_stripe_secret_key()
    return stripe.checkout.Session.retrieve(session_id)


def create_full_refund_for_payment(
    *,
    payment_record: PaymentRecord,
    initiated_by=None,
) -> Any:
    if payment_record.provider != PaymentRecord.PROVIDER_STRIPE:
        raise ValueError("Only Stripe payments can be refunded from this flow.")
    if payment_record.status != PaymentRecord.STATUS_SUCCEEDED:
        raise ValueError("Only successful Stripe payments can be refunded.")
    payment_intent_id = _normalize_external_id(payment_record.external_transaction_id)
    if not payment_intent_id:
        raise ValueError("Stripe payment intent is missing for this payment record.")

    stripe = _import_stripe()
    stripe.api_key = _require_stripe_secret_key()
    try:
        refund = stripe.Refund.create(
            payment_intent=payment_intent_id,
            reason="requested_by_customer",
            metadata={
                "payment_record_id": str(payment_record.id),
                "invoice_id": str(payment_record.invoice_id),
                "payment_reference": payment_record.payment_reference,
            },
            idempotency_key=f"refund-full-{payment_record.id}",
        )
    except Exception as exc:
        error_text = str(exc).lower()
        if "already been refunded" in error_text:
            _apply_local_refunded_state(payment_record)
            return SimpleNamespace(id="", status="succeeded", payment_intent=payment_intent_id)
        raise

    refund_status = str(getattr(refund, "status", "") or "").lower()
    if refund_status == "succeeded":
        _apply_local_refunded_state(payment_record)
    return refund


def finalize_checkout_success_from_redirect(
    *,
    session_id: str,
    payment_status: str | None,
    payment_intent: str | None,
    metadata: dict[str, Any] | None,
) -> tuple[PaymentRecord | None, bool]:
    """
    Temporary fallback for sandbox flow without webhooks.
    Finalizes invoice/payment when user returns from Stripe success_url.
    """
    # Normalize the payload shape so we can reuse existing row-lock lookup logic.
    session_object = {
        "id": session_id,
        "payment_status": payment_status,
        "payment_intent": payment_intent,
        "metadata": metadata or {},
    }

    with transaction.atomic():
        payment_record = _lock_payment_record(session_object)
        if payment_record is None:
            return None, False

        if payment_status != "paid":
            return payment_record, False

        changed = False
        invoice = payment_record.invoice
        update_fields = ["updated_at"]

        # Idempotent update: set succeeded state only when not already finalized.
        if payment_record.status != PaymentRecord.STATUS_SUCCEEDED or payment_record.paid_at is None:
            payment_record.status = PaymentRecord.STATUS_SUCCEEDED
            payment_record.paid_at = timezone.now()
            update_fields.extend(["status", "paid_at"])
            changed = True

        normalized_payment_intent = _normalize_external_id(payment_intent)
        if (
            normalized_payment_intent
            and payment_record.external_transaction_id != normalized_payment_intent
        ):
            payment_record.external_transaction_id = normalized_payment_intent
            update_fields.append("external_transaction_id")
            changed = True

        if changed:
            payment_record.save(update_fields=update_fields)

        invoice_status_before = invoice.status
        if invoice.status != Invoice.STATUS_PAID:
            invoice.status = Invoice.STATUS_PAID
            invoice.save(update_fields=["status", "updated_at"])
            changed = True

        if changed:
            log_event(
                action="payment.stripe.redirect_confirmed",
                user=payment_record.created_by,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "payment_reference": payment_record.payment_reference,
                    "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
                },
            )
        if invoice_status_before != Invoice.STATUS_PAID:
            log_event(
                action="payment.invoice.marked_paid",
                user=payment_record.created_by,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "previous_status": invoice_status_before,
                    "new_status": Invoice.STATUS_PAID,
                    "payment_reference": payment_record.payment_reference,
                    "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
                    "source": "success_redirect",
                },
            )

        return payment_record, changed


def construct_webhook_event(payload: bytes, stripe_signature: str):
    stripe = _import_stripe()
    stripe.api_key = _require_stripe_secret_key()
    webhook_secret = _require_stripe_webhook_secret()
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=stripe_signature,
        secret=webhook_secret,
    )


def _lock_payment_record(session_object: dict[str, Any]) -> PaymentRecord | None:
    session_id = session_object.get("id")
    metadata = session_object.get("metadata") or {}
    # Defensive guard: metadata can come from Stripe objects, not only plain dict payloads.
    if not hasattr(metadata, "get"):
        try:
            metadata = dict(metadata)
        except (TypeError, ValueError):
            metadata = {}
    payment_record_id = metadata.get("payment_record_id")

    queryset = PaymentRecord.objects.select_related("invoice").select_for_update()
    if session_id:
        payment_record = queryset.filter(stripe_checkout_session_id=session_id).first()
        if payment_record:
            return payment_record
    if payment_record_id:
        return queryset.filter(id=payment_record_id).first()
    return None


def _lock_payment_record_for_refund(refund_object: dict[str, Any]) -> PaymentRecord | None:
    metadata = refund_object.get("metadata") or {}
    if not hasattr(metadata, "get"):
        try:
            metadata = dict(metadata)
        except (TypeError, ValueError):
            metadata = {}
    payment_record_id = metadata.get("payment_record_id")
    payment_intent_id = _normalize_external_id(refund_object.get("payment_intent"))

    queryset = PaymentRecord.objects.select_related("invoice").select_for_update()
    if payment_record_id:
        payment_record = queryset.filter(id=payment_record_id).first()
        if payment_record is not None:
            return payment_record
    if payment_intent_id:
        return queryset.filter(external_transaction_id=payment_intent_id).first()
    return None


def _mark_refund_from_event(
    *,
    event_type: str,
    refund_object: dict[str, Any],
    event_record: StripeWebhookEvent,
) -> None:
    refund_status = str(refund_object.get("status") or "").lower()
    refund_id = _normalize_external_id(refund_object.get("id"))
    with transaction.atomic():
        payment_record = _lock_payment_record_for_refund(refund_object)
        if payment_record is None:
            event_record.status = StripeWebhookEvent.STATUS_IGNORED
            event_record.error_message = "Payment record not found for refund event."
            event_record.processed_at = timezone.now()
            event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
            return

        invoice = payment_record.invoice
        invoice_status_before = invoice.status
        if refund_status == "succeeded":
            if payment_record.status != PaymentRecord.STATUS_REFUNDED:
                payment_record.status = PaymentRecord.STATUS_REFUNDED
                payment_record.save(update_fields=["status", "updated_at"])
            if invoice.status == Invoice.STATUS_PAID:
                invoice.status = _resolve_refunded_invoice_status(invoice)
                invoice.save(update_fields=["status", "updated_at"])
            log_event(
                action="payment.refund.succeeded",
                user=None,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "payment_reference": payment_record.payment_reference,
                    "refund_status": refund_status,
                    "refund_id": refund_id,
                    "event_type": event_type,
                    "previous_invoice_status": invoice_status_before,
                    "new_invoice_status": invoice.status,
                },
            )
        elif refund_status in {"failed", "canceled"}:
            log_event(
                action="payment.refund.failed",
                user=None,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "payment_reference": payment_record.payment_reference,
                    "refund_status": refund_status,
                    "refund_id": refund_id,
                    "event_type": event_type,
                },
            )
        else:
            log_event(
                action="payment.refund.requested",
                user=None,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "payment_reference": payment_record.payment_reference,
                    "refund_status": refund_status or "unknown",
                    "refund_id": refund_id,
                    "event_type": event_type,
                },
            )

        event_record.status = StripeWebhookEvent.STATUS_PROCESSED
        event_record.payment_record = payment_record
        event_record.invoice = invoice
        event_record.processed_at = timezone.now()
        event_record.error_message = ""
        event_record.save(
            update_fields=[
                "status",
                "payment_record",
                "invoice",
                "processed_at",
                "error_message",
                "updated_at",
            ]
        )

        event_record.payload = event_record.payload or {}
        payload_data = event_record.payload.get("data") or {}
        payload_object = payload_data.get("object") or {}
        payload_object["normalized_refund_status"] = refund_status
        payload_data["object"] = payload_object
        event_record.payload["data"] = payload_data
        event_record.save(update_fields=["payload", "updated_at"])


def _mark_success_from_session(
    *,
    event_type: str,
    session_object: dict[str, Any],
    event_record: StripeWebhookEvent,
) -> None:
    payment_status = session_object.get("payment_status")
    if payment_status != "paid":
        event_record.status = StripeWebhookEvent.STATUS_IGNORED
        event_record.error_message = (
            f"Ignored {event_type}: Checkout session payment_status={payment_status!r}."
        )
        event_record.processed_at = timezone.now()
        event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        return

    with transaction.atomic():
        payment_record = _lock_payment_record(session_object)
        if payment_record is None:
            event_record.status = StripeWebhookEvent.STATUS_IGNORED
            event_record.error_message = "Payment record not found for checkout session."
            event_record.processed_at = timezone.now()
            event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
            return

        invoice = payment_record.invoice
        payment_intent_id = _normalize_external_id(session_object.get("payment_intent"))
        update_fields = ["status", "paid_at", "updated_at"]
        payment_record.status = PaymentRecord.STATUS_SUCCEEDED
        payment_record.paid_at = timezone.now()
        if payment_intent_id and payment_record.external_transaction_id != payment_intent_id:
            payment_record.external_transaction_id = payment_intent_id
            update_fields.append("external_transaction_id")
        payment_record.save(update_fields=update_fields)

        invoice_status_before = invoice.status
        if invoice.status != Invoice.STATUS_PAID:
            invoice.status = Invoice.STATUS_PAID
            invoice.save(update_fields=["status", "updated_at"])

        event_record.status = StripeWebhookEvent.STATUS_PROCESSED
        event_record.payment_record = payment_record
        event_record.invoice = invoice
        event_record.processed_at = timezone.now()
        event_record.error_message = ""
        event_record.save(
            update_fields=[
                "status",
                "payment_record",
                "invoice",
                "processed_at",
                "error_message",
                "updated_at",
            ]
        )

        log_event(
            action="payment.stripe.succeeded",
            user=None,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
                "event_type": event_type,
            },
        )
        if invoice_status_before != Invoice.STATUS_PAID:
            log_event(
                action="payment.invoice.marked_paid",
                user=None,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "previous_status": invoice_status_before,
                    "new_status": Invoice.STATUS_PAID,
                    "payment_reference": payment_record.payment_reference,
                    "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
                },
            )


def _log_non_success_event(
    *,
    event_type: str,
    payment_record: PaymentRecord,
    final_status: str,
) -> None:
    action = (
        "payment.stripe.cancelled"
        if final_status == PaymentRecord.STATUS_CANCELLED
        else "payment.stripe.failed"
    )
    log_event(
        action=action,
        user=payment_record.created_by,
        target_type="invoice",
        target_id=str(payment_record.invoice_id),
        metadata={
            "invoice_number": payment_record.invoice.invoice_number,
            "payment_reference": payment_record.payment_reference,
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
            "event_type": event_type,
            "payment_status": final_status,
        },
    )


def _mark_non_success_from_session(
    *,
    event_type: str,
    session_object: dict[str, Any],
    event_record: StripeWebhookEvent,
    final_status: str,
) -> None:
    with transaction.atomic():
        payment_record = _lock_payment_record(session_object)
        if payment_record is None:
            event_record.status = StripeWebhookEvent.STATUS_IGNORED
            event_record.error_message = "Payment record not found for checkout session."
            event_record.processed_at = timezone.now()
            event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
            return

        if payment_record.status != PaymentRecord.STATUS_SUCCEEDED:
            payment_record.status = final_status
            payment_record.save(update_fields=["status", "updated_at"])

        event_record.status = StripeWebhookEvent.STATUS_PROCESSED
        event_record.payment_record = payment_record
        event_record.invoice = payment_record.invoice
        event_record.processed_at = timezone.now()
        event_record.error_message = ""
        event_record.save(
            update_fields=[
                "status",
                "payment_record",
                "invoice",
                "processed_at",
                "error_message",
                "updated_at",
            ]
        )
        _log_non_success_event(
            event_type=event_type,
            payment_record=payment_record,
            final_status=final_status,
        )


def process_webhook_event(event_payload: dict[str, Any]) -> tuple[StripeWebhookEvent, bool]:
    event_id = event_payload.get("id")
    event_type = event_payload.get("type")
    if not event_id or not event_type:
        raise ValueError("Stripe webhook payload missing id or type.")

    created = False
    try:
        with transaction.atomic():
            event_record = StripeWebhookEvent.objects.create(
                event_id=event_id,
                event_type=event_type,
                status=StripeWebhookEvent.STATUS_RECEIVED,
                payload=event_payload,
            )
        created = True
    except IntegrityError:
        event_record = StripeWebhookEvent.objects.get(event_id=event_id)
        # True duplicates are acknowledged without reprocessing.
        if event_record.status in {StripeWebhookEvent.STATUS_PROCESSED, StripeWebhookEvent.STATUS_IGNORED}:
            return event_record, False
        # Retry events that were previously left in received/failed state.
        event_record.payload = event_payload
        event_record.error_message = ""
        event_record.save(update_fields=["payload", "error_message", "updated_at"])
        created = True

    try:
        if event_type not in SUPPORTED_WEBHOOK_EVENTS:
            event_record.status = StripeWebhookEvent.STATUS_IGNORED
            event_record.error_message = f"Unsupported event type: {event_type}"
            event_record.processed_at = timezone.now()
            event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
            return event_record, created

        event_object = (event_payload.get("data") or {}).get("object") or {}

        if event_type in {WEBHOOK_EVENT_COMPLETED, WEBHOOK_EVENT_ASYNC_SUCCEEDED}:
            _mark_success_from_session(
                event_type=event_type,
                session_object=event_object,
                event_record=event_record,
            )
            return event_record, created

        if event_type == WEBHOOK_EVENT_ASYNC_FAILED:
            _mark_non_success_from_session(
                event_type=event_type,
                session_object=event_object,
                event_record=event_record,
                final_status=PaymentRecord.STATUS_FAILED,
            )
            return event_record, created

        if event_type == WEBHOOK_EVENT_EXPIRED:
            _mark_non_success_from_session(
                event_type=event_type,
                session_object=event_object,
                event_record=event_record,
                final_status=PaymentRecord.STATUS_CANCELLED,
            )
            return event_record, created

        if event_type in {
            WEBHOOK_EVENT_REFUND_CREATED,
            WEBHOOK_EVENT_REFUND_UPDATED,
            WEBHOOK_EVENT_REFUND_FAILED,
        }:
            _mark_refund_from_event(
                event_type=event_type,
                refund_object=event_object,
                event_record=event_record,
            )
            return event_record, created

        event_record.status = StripeWebhookEvent.STATUS_IGNORED
        event_record.error_message = f"Unhandled event type: {event_type}"
        event_record.processed_at = timezone.now()
        event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        return event_record, created
    except Exception as exc:
        event_record.status = StripeWebhookEvent.STATUS_FAILED
        event_record.error_message = str(exc)
        event_record.processed_at = timezone.now()
        event_record.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        raise
