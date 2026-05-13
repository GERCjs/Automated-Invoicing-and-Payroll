from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.roles import CUSTOMER
from core.audit import get_client_ip, log_event
from invoicing.models import Invoice
from invoicing.services import apply_overdue_status

from .models import PaymentRecord
from .services import (
    construct_webhook_event,
    create_checkout_for_invoice,
    process_webhook_event,
    retrieve_checkout_session,
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

    try:
        payment_record = create_checkout_for_invoice(
            invoice=invoice,
            success_url=_checkout_success_url(request),
            cancel_url=cancel_url,
            initiated_by=request.user if request.user.is_authenticated else None,
        )
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


def checkout_success(request):
    session_id = request.GET.get("session_id", "").strip()
    invoice = None
    payment_record = None
    payment_status = None

    if session_id:
        try:
            session = retrieve_checkout_session(session_id)
        except Exception:
            session = None
        if session is not None:
            payment_status = session.payment_status
            payment_record = PaymentRecord.objects.select_related("invoice").filter(
                stripe_checkout_session_id=session_id
            ).first()
            if payment_record is not None:
                invoice = payment_record.invoice

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
    return render(
        request,
        "payments/checkout_cancel.html",
        {"next_url": next_url},
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
    except Exception:
        return HttpResponseBadRequest("Invalid webhook signature.")

    event_record, created = process_webhook_event(event)
    if not created:
        return HttpResponse(status=200)

    if event_record.status == event_record.STATUS_FAILED:
        return HttpResponse(status=500)
    return HttpResponse(status=200)
