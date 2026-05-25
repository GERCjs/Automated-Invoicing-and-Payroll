from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, SUPERADMIN
from core.audit import get_client_ip, log_event
from invoicing.models import Invoice
from invoicing.services import apply_overdue_status
from notifications.services import (
    send_stripe_refund_failed_email,
    send_stripe_refund_success_email,
    send_stripe_payment_failed_email,
    send_stripe_payment_success_email,
)

from .models import PaymentRecord
from .services import (
    WEBHOOK_EVENT_ASYNC_FAILED,
    WEBHOOK_EVENT_ASYNC_SUCCEEDED,
    WEBHOOK_EVENT_COMPLETED,
    WEBHOOK_EVENT_EXPIRED,
    WEBHOOK_EVENT_REFUND_CREATED,
    WEBHOOK_EVENT_REFUND_FAILED,
    WEBHOOK_EVENT_REFUND_UPDATED,
    construct_webhook_event,
    create_full_refund_for_payment,
    create_checkout_for_invoice,
    finalize_checkout_success_from_redirect,
    process_webhook_event,
    retrieve_checkout_session,
)

PAYABLE_INVOICE_STATUSES = {
    Invoice.STATUS_SENT,
    Invoice.STATUS_VIEWED,
    Invoice.STATUS_OVERDUE,
}


def _public_invoice_url(request, invoice: Invoice) -> str:
    return request.build_absolute_uri(reverse("invoice-public-view", args=[invoice.public_view_token]))


def _send_success_payment_email(request, payment_record: PaymentRecord) -> None:
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_payment_success_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    log_event(
        action="payment.email.sent" if success else "payment.email.failed",
        user=request.user if request.user.is_authenticated else payment_record.created_by,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "delivery_log_id": delivery_log.id,
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "successful",
            "recipient_email": invoice.customer.email,
            "error_message": delivery_log.error_message,
        },
        ip_address=get_client_ip(request),
    )


def _send_failed_payment_email(
    request,
    payment_record: PaymentRecord,
    *,
    failure_reason: str,
) -> None:
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_payment_failed_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        failure_reason=failure_reason,
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    log_event(
        action="payment.email.sent" if success else "payment.email.failed",
        user=request.user if request.user.is_authenticated else payment_record.created_by,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "delivery_log_id": delivery_log.id,
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "failed",
            "recipient_email": invoice.customer.email,
            "failure_reason": failure_reason,
            "error_message": delivery_log.error_message,
        },
        ip_address=get_client_ip(request),
    )


def _send_success_refund_email(request, payment_record: PaymentRecord) -> None:
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_refund_success_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    log_event(
        action="payment.email.sent" if success else "payment.email.failed",
        user=request.user if request.user.is_authenticated else payment_record.created_by,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "delivery_log_id": delivery_log.id,
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "refund_successful",
            "recipient_email": invoice.customer.email,
            "error_message": delivery_log.error_message,
        },
        ip_address=get_client_ip(request),
    )


def _send_failed_refund_email(
    request,
    payment_record: PaymentRecord,
    *,
    failure_reason: str,
) -> None:
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_refund_failed_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        failure_reason=failure_reason,
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    log_event(
        action="payment.email.sent" if success else "payment.email.failed",
        user=request.user if request.user.is_authenticated else payment_record.created_by,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "delivery_log_id": delivery_log.id,
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "refund_failed",
            "recipient_email": invoice.customer.email,
            "failure_reason": failure_reason,
            "error_message": delivery_log.error_message,
        },
        ip_address=get_client_ip(request),
    )


def _checkout_success_url(request) -> str:
    return request.build_absolute_uri(reverse("payment-checkout-success")) + "?session_id={CHECKOUT_SESSION_ID}"


def _checkout_cancel_url_for_public(request, token: str) -> str:
    invoice_url = reverse("invoice-public-view", args=[token])
    return request.build_absolute_uri(f"{reverse('payment-checkout-cancel')}?next={invoice_url}")


def _checkout_cancel_url_for_customer(request, pk: int) -> str:
    invoice_url = reverse("customer-invoice-detail", args=[pk])
    return request.build_absolute_uri(f"{reverse('payment-checkout-cancel')}?next={invoice_url}")


def _start_checkout(request, *, invoice: Invoice, cancel_url: str, fallback_url: str):
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    if invoice.status == Invoice.STATUS_PAID:
        messages.info(request, f"Invoice {invoice.invoice_number} is already paid.")
        return redirect(fallback_url)
    if invoice.status not in PAYABLE_INVOICE_STATUSES:
        messages.error(
            request,
            f"Invoice {invoice.invoice_number} is not ready for payment yet.",
        )
        return redirect(fallback_url)

    try:
        payment_record = create_checkout_for_invoice(
            invoice=invoice,
            success_url=_checkout_success_url(request),
            cancel_url=cancel_url,
            initiated_by=request.user if request.user.is_authenticated else None,
        )
    except ImproperlyConfigured as exc:
        log_event(
            action="payment.checkout.configuration_error",
            user=request.user if request.user.is_authenticated else None,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={"invoice_number": invoice.invoice_number, "reason": str(exc)},
            ip_address=get_client_ip(request),
        )
        messages.error(request, str(exc))
        return redirect(fallback_url)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(fallback_url)
    except Exception:
        messages.error(request, "Stripe is not configured correctly yet. Please contact support.")
        return redirect(fallback_url)

    log_event(
        action="payment.checkout.started",
        user=request.user if request.user.is_authenticated else None,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "payment_reference": payment_record.payment_reference,
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
            "path": request.path,
        },
        ip_address=get_client_ip(request),
    )
    session_url = ""
    if payment_record.stripe_checkout_session_id:
        try:
            session = retrieve_checkout_session(payment_record.stripe_checkout_session_id)
            session_url = session.url or ""
        except Exception:
            session_url = ""
    if not session_url:
        messages.error(request, "Unable to start Stripe Checkout session.")
        return redirect(fallback_url)
    return redirect(session_url)


@require_POST
def checkout_public_invoice(request, token):
    invoice = (
        Invoice.objects.select_related("customer")
        .filter(public_view_token=token)
        .order_by("id")
        .first()
    )
    if invoice is None:
        raise Http404()
    return _start_checkout(
        request,
        invoice=invoice,
        cancel_url=_checkout_cancel_url_for_public(request, token),
        fallback_url=reverse("invoice-public-view", args=[token]),
    )


@login_required
@role_required(CUSTOMER)
@require_POST
def checkout_customer_invoice(request, pk):
    user_email = (request.user.email or "").strip()
    if not user_email:
        raise Http404()
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer"),
        pk=pk,
        customer__email__iexact=user_email,
    )
    return _start_checkout(
        request,
        invoice=invoice,
        cancel_url=_checkout_cancel_url_for_customer(request, pk),
        fallback_url=reverse("customer-invoice-detail", args=[pk]),
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
@require_POST
def refund_invoice_payment(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    payment_record = (
        PaymentRecord.objects.select_related("invoice")
        .filter(
            invoice=invoice,
            provider=PaymentRecord.PROVIDER_STRIPE,
        )
        .order_by("-created_at")
        .first()
    )
    if payment_record is None:
        log_event(
            action="payment.refund.failed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "reason": "No Stripe payment record found for invoice.",
            },
            ip_address=get_client_ip(request),
        )
        messages.error(request, "No Stripe payment record was found for this invoice.")
        return redirect("invoice-detail", pk=invoice.pk)
    if payment_record.status == PaymentRecord.STATUS_REFUNDED:
        log_event(
            action="payment.refund.requested",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "current_status": payment_record.status,
                "result": "already_refunded",
            },
            ip_address=get_client_ip(request),
        )
        messages.info(request, "This Stripe payment is already refunded.")
        return redirect("invoice-detail", pk=invoice.pk)

    log_event(
        action="payment.refund.requested",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "payment_reference": payment_record.payment_reference,
            "current_status": payment_record.status,
        },
        ip_address=get_client_ip(request),
    )

    try:
        invoice_status_before = invoice.status
        refund = create_full_refund_for_payment(
            payment_record=payment_record,
            initiated_by=request.user,
        )
    except ImproperlyConfigured as exc:
        log_event(
            action="payment.refund.failed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "reason": str(exc),
            },
            ip_address=get_client_ip(request),
        )
        messages.error(request, str(exc))
        return redirect("invoice-detail", pk=invoice.pk)
    except ValueError as exc:
        log_event(
            action="payment.refund.failed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "reason": str(exc),
            },
            ip_address=get_client_ip(request),
        )
        messages.error(request, str(exc))
        return redirect("invoice-detail", pk=invoice.pk)
    except Exception as exc:
        log_event(
            action="payment.refund.failed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "reason": f"Stripe refund request failed: {exc}",
            },
            ip_address=get_client_ip(request),
        )
        messages.error(request, "Stripe refund request failed. Please try again later.")
        return redirect("invoice-detail", pk=invoice.pk)

    refund_status = str(getattr(refund, "status", "") or "").lower()
    payment_record.refresh_from_db()
    invoice.refresh_from_db()
    if refund_status == "succeeded":
        _send_success_refund_email(request, payment_record)
        log_event(
            action="payment.refund.succeeded",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "refund_status": refund_status,
                "previous_invoice_status": invoice_status_before,
                "new_invoice_status": invoice.status,
            },
            ip_address=get_client_ip(request),
        )
        messages.success(request, f"Stripe refund succeeded for invoice {invoice.invoice_number}.")
    else:
        _send_failed_refund_email(
            request,
            payment_record,
            failure_reason=f"Stripe refund status: {refund_status or 'unknown'}.",
        )
        log_event(
            action="payment.refund.requested",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "refund_status": refund_status or "unknown",
                "result": "pending_or_non_final",
            },
            ip_address=get_client_ip(request),
        )
        messages.info(
            request,
            (
                f"Stripe refund was created for invoice {invoice.invoice_number} "
                f"with status '{refund_status or 'unknown'}'."
            ),
        )
    return redirect("invoice-detail", pk=invoice.pk)


def checkout_success(request):
    session_id = request.GET.get("session_id", "").strip()
    invoice = None
    payment_record = None
    payment_status = None

    if session_id:
        try:
            session = retrieve_checkout_session(session_id)
        except ImproperlyConfigured as exc:
            messages.error(request, str(exc))
            session = None
        except Exception:
            session = None
        if session is not None:
            payment_status = session.payment_status
            raw_metadata = getattr(session, "metadata", None)
            if hasattr(raw_metadata, "to_dict_recursive"):
                # StripeObject metadata needs explicit conversion instead of dict(...).
                metadata = raw_metadata.to_dict_recursive()
            elif isinstance(raw_metadata, dict):
                metadata = raw_metadata
            else:
                metadata = {}
            # Sandbox fallback: finalize status from redirect return when webhook is disabled.
            payment_record, _ = finalize_checkout_success_from_redirect(
                session_id=session_id,
                payment_status=getattr(session, "payment_status", None),
                payment_intent=getattr(session, "payment_intent", None),
                metadata=metadata,
            )
            if payment_record is None:
                payment_record = PaymentRecord.objects.select_related("invoice").filter(
                    stripe_checkout_session_id=session_id
                ).first()
            if payment_record is not None:
                invoice = payment_record.invoice
                if payment_record.status == PaymentRecord.STATUS_SUCCEEDED:
                    _send_success_payment_email(request, payment_record)

    return render(
        request,
        "payments/checkout_success.html",
        {
            "session_id": session_id,
            "payment_status": payment_status,
            "invoice": invoice,
            "payment_record": payment_record,
        },
    )


def checkout_cancel(request):
    next_url = request.GET.get("next", "").strip()
    session_id = request.GET.get("session_id", "").strip()
    payment_reference = request.GET.get("payment_reference", "").strip()

    payment_record = None
    if session_id:
        payment_record = PaymentRecord.objects.select_related("invoice").filter(
            stripe_checkout_session_id=session_id
        ).first()
    if payment_record is None and payment_reference:
        payment_record = PaymentRecord.objects.select_related("invoice").filter(
            payment_reference=payment_reference
        ).first()

    if (
        payment_record is not None
        and payment_record.status
        not in {PaymentRecord.STATUS_SUCCEEDED, PaymentRecord.STATUS_CANCELLED}
    ):
        payment_record.status = PaymentRecord.STATUS_CANCELLED
        payment_record.save(update_fields=["status", "updated_at"])

    if payment_record is not None:
        log_event(
            action="payment.checkout.cancelled",
            user=request.user if request.user.is_authenticated else payment_record.created_by,
            target_type="invoice",
            target_id=str(payment_record.invoice_id),
            metadata={
                "invoice_number": payment_record.invoice.invoice_number,
                "payment_reference": payment_record.payment_reference,
                "stripe_checkout_session_id": payment_record.stripe_checkout_session_id,
                "path": request.path,
                "next_url": next_url,
            },
            ip_address=get_client_ip(request),
        )
        if payment_record.status == PaymentRecord.STATUS_CANCELLED:
            _send_failed_payment_email(
                request,
                payment_record,
                failure_reason="Stripe Checkout was cancelled before payment was completed.",
            )
    else:
        log_event(
            action="payment.checkout.cancelled",
            user=request.user if request.user.is_authenticated else None,
            metadata={
                "path": request.path,
                "next_url": next_url,
                "session_id": session_id,
                "payment_reference": payment_reference,
            },
            ip_address=get_client_ip(request),
        )

    return render(
        request,
        "payments/checkout_cancel.html",
        {"next_url": next_url, "payment_record": payment_record},
    )


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    if not signature:
        return HttpResponseBadRequest("Missing Stripe-Signature header.")

    try:
        event = construct_webhook_event(payload=payload, stripe_signature=signature)
    except ValueError:
        return HttpResponseBadRequest("Invalid payload.")
    except ImproperlyConfigured as exc:
        log_event(
            action="payment.webhook.configuration_error",
            user=None,
            metadata={"reason": str(exc), "path": request.path},
            ip_address=get_client_ip(request),
        )
        return HttpResponse("Stripe webhook is not configured.", status=500)
    except Exception:
        return HttpResponseBadRequest("Invalid webhook signature.")

    event_record, created = process_webhook_event(event)
    if not created:
        return HttpResponse(status=200)

    if event_record.status == event_record.STATUS_FAILED:
        return HttpResponse(status=500)

    if event_record.status == event_record.STATUS_PROCESSED and event_record.payment_record_id:
        payment_record = event_record.payment_record
        if event_record.event_type in {WEBHOOK_EVENT_COMPLETED, WEBHOOK_EVENT_ASYNC_SUCCEEDED}:
            _send_success_payment_email(request, payment_record)
        elif event_record.event_type == WEBHOOK_EVENT_ASYNC_FAILED:
            _send_failed_payment_email(
                request,
                payment_record,
                failure_reason="Stripe reported that the payment failed.",
            )
        elif event_record.event_type == WEBHOOK_EVENT_EXPIRED:
            _send_failed_payment_email(
                request,
                payment_record,
                failure_reason="Stripe Checkout expired before payment was completed.",
            )
        elif event_record.event_type in {
            WEBHOOK_EVENT_REFUND_CREATED,
            WEBHOOK_EVENT_REFUND_UPDATED,
            WEBHOOK_EVENT_REFUND_FAILED,
        }:
            refund_object = ((event_record.payload or {}).get("data") or {}).get("object") or {}
            refund_status = str(refund_object.get("normalized_refund_status") or refund_object.get("status") or "").lower()
            if refund_status == "succeeded":
                _send_success_refund_email(request, payment_record)
            elif refund_status in {"failed", "canceled"}:
                failure_reason = str(refund_object.get("failure_reason") or "").strip()
                _send_failed_refund_email(
                    request,
                    payment_record,
                    failure_reason=failure_reason or f"Stripe refund status: {refund_status}.",
                )
    return HttpResponse(status=200)
