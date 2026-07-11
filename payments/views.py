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
    send_bank_transfer_payment_success_email,
    send_stripe_refund_failed_email,
    send_stripe_refund_success_email,
    send_stripe_payment_failed_email,
    send_stripe_payment_success_email,
)

from .forms import BankTransferConfirmationForm, BankTransferNoticeForm, PaymentBankDetailsForm
from .models import PaymentBankDetails, PaymentRecord
from .services import (
    WEBHOOK_EVENT_ASYNC_FAILED,
    WEBHOOK_EVENT_ASYNC_SUCCEEDED,
    WEBHOOK_EVENT_COMPLETED,
    WEBHOOK_EVENT_EXPIRED,
    WEBHOOK_EVENT_REFUND_CREATED,
    WEBHOOK_EVENT_REFUND_FAILED,
    WEBHOOK_EVENT_REFUND_UPDATED,
    construct_webhook_event,
    confirm_bank_transfer_payment,
    create_full_refund_for_payment,
    create_checkout_for_invoice,
    finalize_checkout_success_from_redirect,
    get_bank_transfer_details,
    get_or_create_bank_transfer_payment,
    mask_bank_account_number,
    process_webhook_event,
    retrieve_checkout_session,
    submit_bank_transfer_notice,
)

# Invoices in these statuses are allowed to go to payment.
PAYABLE_INVOICE_STATUSES = {
    Invoice.STATUS_SENT,
    Invoice.STATUS_VIEWED,
    Invoice.STATUS_OVERDUE,
}


def _public_invoice_url(request, invoice: Invoice) -> str:
    # Build the full public invoice link used inside payment emails.
    return request.build_absolute_uri(reverse("invoice-public-view", args=[invoice.public_view_token]))


def _bank_transfer_notice_success(
    request,
    *,
    invoice: Invoice,
    payment_record: PaymentRecord,
    submitted_by=None,
) -> None:
    log_event(
        action="payment.bank_transfer.notice_submitted",
        user=submitted_by,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "payment_reference": payment_record.payment_reference,
            "payment_status": payment_record.status,
            "manual_customer_amount": str(payment_record.manual_customer_amount),
            "manual_customer_transfer_date": str(payment_record.manual_customer_transfer_date),
            "manual_customer_bank_reference": payment_record.manual_customer_bank_reference,
            "has_proof": bool(payment_record.manual_customer_proof),
        },
        ip_address=get_client_ip(request),
    )
    messages.success(
        request,
        "Your bank transfer notice has been received. Finance will verify it against the company bank account.",
    )


def _render_customer_invoice_with_bank_transfer_form(request, invoice: Invoice, form: BankTransferNoticeForm):
    from notifications.services import get_invoice_reminder_history

    reminder_history = list(get_invoice_reminder_history(invoice))
    sent_reminder_history = [log for log in reminder_history if log.status == "sent"]
    return render(
        request,
        "invoicing/customer_invoice_detail.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            "bank_transfer_details": get_bank_transfer_details(),
            "bank_transfer_payment": form.payment_record,
            "bank_transfer_notice_form": form,
            "bank_transfer_notice_action": reverse("payment-bank-transfer-notice-customer", args=[invoice.pk]),
            "reminder_history": reminder_history,
            "reminders_sent_count": len(sent_reminder_history),
            "last_reminder_sent_at": (
                (sent_reminder_history[0].sent_at or sent_reminder_history[0].attempted_at)
                if sent_reminder_history
                else None
            ),
        },
        status=400,
    )


def _render_public_invoice_with_bank_transfer_form(request, invoice: Invoice, form: BankTransferNoticeForm):
    return render(
        request,
        "invoicing/invoice_public_view.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            "bank_transfer_details": get_bank_transfer_details(),
            "bank_transfer_payment": form.payment_record,
            "bank_transfer_notice_form": form,
            "bank_transfer_notice_action": reverse(
                "payment-bank-transfer-notice-public",
                args=[invoice.public_view_token],
            ),
        },
        status=400,
    )


def _send_success_payment_email(request, payment_record: PaymentRecord) -> None:
    # Send an email to the customer after a successful Stripe payment.
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_payment_success_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    # Record whether the email was sent successfully.
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


def _send_bank_transfer_success_payment_email(request, payment_record: PaymentRecord) -> None:
    invoice = payment_record.invoice
    success, delivery_log = send_bank_transfer_payment_success_email(
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
            "payment_outcome": "bank_transfer_successful",
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
    # Send an email to the customer after a failed or cancelled Stripe payment.
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_payment_failed_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        failure_reason=failure_reason,
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    # Record whether the email was sent successfully.
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
    # Send an email to the customer after a successful refund.
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_refund_success_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    # Record whether the refund email was sent successfully.
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
    # Send an email to the customer if a refund fails.
    invoice = payment_record.invoice
    success, delivery_log = send_stripe_refund_failed_email(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=_public_invoice_url(request, invoice),
        failure_reason=failure_reason,
        triggered_by=request.user if request.user.is_authenticated else payment_record.created_by,
    )
    # Record whether the refund failure email was sent successfully.
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
    # Stripe replaces CHECKOUT_SESSION_ID with the real session ID after payment.
    return request.build_absolute_uri(reverse("payment-checkout-success")) + "?session_id={CHECKOUT_SESSION_ID}"


def _checkout_cancel_url_for_public(request, token: str) -> str:
    # Send public users back to the public invoice page after cancellation.
    invoice_url = reverse("invoice-public-view", args=[token])
    return request.build_absolute_uri(f"{reverse('payment-checkout-cancel')}?next={invoice_url}")


def _checkout_cancel_url_for_customer(request, pk: int) -> str:
    # Send logged-in customers back to their invoice detail page after cancellation.
    invoice_url = reverse("customer-invoice-detail", args=[pk])
    return request.build_absolute_uri(f"{reverse('payment-checkout-cancel')}?next={invoice_url}")


def _start_checkout(request, *, invoice: Invoice, cancel_url: str, fallback_url: str):
    # Shared helper used by both public and logged-in customer checkout.
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    # Do not let users pay an invoice that is already paid.
    if invoice.status == Invoice.STATUS_PAID:
        messages.info(request, f"Invoice {invoice.invoice_number} is already paid.")
        return redirect(fallback_url)
    # Only sent/viewed/overdue invoices can be paid.
    if invoice.status not in PAYABLE_INVOICE_STATUSES:
        messages.error(
            request,
            f"Invoice {invoice.invoice_number} is not ready for payment yet.",
        )
        return redirect(fallback_url)

    try:
        # Create the local payment record and Stripe Checkout session.
        payment_record = create_checkout_for_invoice(
            invoice=invoice,
            success_url=_checkout_success_url(request),
            cancel_url=cancel_url,
            initiated_by=request.user if request.user.is_authenticated else None,
        )
    except ImproperlyConfigured as exc:
        # Missing Stripe settings are logged and shown to the user.
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
        # Business-rule errors, such as an already-paid invoice, come here.
        messages.error(request, str(exc))
        return redirect(fallback_url)
    except Exception:
        # Any unexpected Stripe setup problem gives a safe user-facing message.
        messages.error(request, "Stripe is not configured correctly yet. Please contact support.")
        return redirect(fallback_url)

    # Audit that checkout was started.
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
            # Retrieve the Stripe-hosted checkout URL.
            session = retrieve_checkout_session(payment_record.stripe_checkout_session_id)
            session_url = session.url or ""
        except Exception:
            # If Stripe lookup fails, keep session_url blank and show an error below.
            session_url = ""
    if not session_url:
        messages.error(request, "Unable to start Stripe Checkout session.")
        return redirect(fallback_url)
    # Send the user to Stripe's hosted payment page.
    return redirect(session_url)


@require_POST
def checkout_public_invoice(request, token):
    # Public invoice payment uses the public invoice token instead of login.
    invoice = (
        Invoice.objects.select_related("customer")
        .filter(public_view_token=token)
        .order_by("id")
        .first()
    )
    if invoice is None:
        # A bad or unknown token should behave like a missing page.
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
    # Logged-in customers can only pay invoices that match their email address.
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
@role_required(CUSTOMER)
@require_POST
def submit_customer_bank_transfer_notice(request, pk):
    user_email = (request.user.email or "").strip()
    if not user_email:
        raise Http404()
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer"),
        pk=pk,
        customer__email__iexact=user_email,
    )
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    if invoice.status not in PAYABLE_INVOICE_STATUSES:
        messages.error(request, "Bank transfer notice can only be submitted for payable invoices.")
        return redirect("customer-invoice-detail", pk=invoice.pk)

    payment_record = get_or_create_bank_transfer_payment(invoice=invoice, initiated_by=request.user)
    form = BankTransferNoticeForm(request.POST, request.FILES, payment_record=payment_record)
    if not form.is_valid():
        return _render_customer_invoice_with_bank_transfer_form(request, invoice, form)

    try:
        payment_record = submit_bank_transfer_notice(
            payment_record=payment_record,
            transferred_amount=form.cleaned_data["manual_customer_amount"],
            transfer_date=form.cleaned_data["manual_customer_transfer_date"],
            bank_reference=form.cleaned_data["manual_customer_bank_reference"],
            notes=form.cleaned_data["manual_customer_notes"],
            proof=form.cleaned_data.get("manual_customer_proof"),
            submitted_by=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("customer-invoice-detail", pk=invoice.pk)

    _bank_transfer_notice_success(
        request,
        invoice=invoice,
        payment_record=payment_record,
        submitted_by=request.user,
    )
    return redirect("customer-invoice-detail", pk=invoice.pk)


@require_POST
def submit_public_bank_transfer_notice(request, token):
    invoice = (
        Invoice.objects.select_related("customer")
        .filter(public_view_token=token)
        .order_by("id")
        .first()
    )
    if invoice is None:
        raise Http404()
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    if invoice.status not in PAYABLE_INVOICE_STATUSES:
        messages.error(request, "Bank transfer notice can only be submitted for payable invoices.")
        return redirect("invoice-public-view", token=invoice.public_view_token)

    payment_record = get_or_create_bank_transfer_payment(invoice=invoice)
    form = BankTransferNoticeForm(request.POST, request.FILES, payment_record=payment_record)
    if not form.is_valid():
        return _render_public_invoice_with_bank_transfer_form(request, invoice, form)

    try:
        payment_record = submit_bank_transfer_notice(
            payment_record=payment_record,
            transferred_amount=form.cleaned_data["manual_customer_amount"],
            transfer_date=form.cleaned_data["manual_customer_transfer_date"],
            bank_reference=form.cleaned_data["manual_customer_bank_reference"],
            notes=form.cleaned_data["manual_customer_notes"],
            proof=form.cleaned_data.get("manual_customer_proof"),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("invoice-public-view", token=invoice.public_view_token)

    _bank_transfer_notice_success(
        request,
        invoice=invoice,
        payment_record=payment_record,
    )
    return redirect("invoice-public-view", token=invoice.public_view_token)


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
@require_POST
def confirm_bank_transfer_payment_for_invoice(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    payment_record = (
        PaymentRecord.objects.select_related("invoice")
        .filter(
            invoice=invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
        )
        .order_by("created_at")
        .first()
    )
    if payment_record is None:
        messages.error(request, "No pending bank transfer reference was found for this invoice.")
        return redirect("invoice-detail", pk=invoice.pk)

    form = BankTransferConfirmationForm(request.POST, payment_record=payment_record)
    if not form.is_valid():
        for error in form.non_field_errors():
            messages.error(request, error)
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return render(
            request,
            "invoicing/invoice_detail.html",
            {
                "invoice": invoice,
                "items": invoice.items.all(),
                "status_choices": Invoice.STATUS_CHOICES,
                "bank_transfer_details": PaymentBankDetails.load().as_display_dict(),
                "bank_transfer_payment": payment_record,
                "bank_transfer_confirmation_form": form,
            },
            status=400,
        )

    try:
        payment_record, invoice_status_before, changed = confirm_bank_transfer_payment(
            payment_record=payment_record,
            received_amount=form.cleaned_data["manual_received_amount"],
            received_date=form.cleaned_data["manual_received_date"],
            bank_reference=form.cleaned_data["manual_bank_reference"],
            confirmation_notes=form.cleaned_data["manual_confirmation_notes"],
            confirmed_by=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("invoice-detail", pk=invoice.pk)

    log_event(
        action="payment.bank_transfer.confirmed",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "payment_reference": payment_record.payment_reference,
            "payment_status": payment_record.status,
            "manual_received_amount": str(payment_record.manual_received_amount),
            "manual_received_date": str(payment_record.manual_received_date),
            "manual_bank_reference": payment_record.manual_bank_reference,
            "manual_confirmed_by": request.user.username,
            "manual_customer_amount": str(payment_record.manual_customer_amount),
            "manual_customer_transfer_date": str(payment_record.manual_customer_transfer_date),
            "manual_customer_bank_reference": payment_record.manual_customer_bank_reference,
            "manual_customer_submitted_at": str(payment_record.manual_customer_submitted_at),
            "invoice_status_before": invoice_status_before,
            "invoice_status_after": Invoice.STATUS_PAID,
            "changed": changed,
        },
        ip_address=get_client_ip(request),
    )
    if invoice_status_before != Invoice.STATUS_PAID:
        log_event(
            action="payment.invoice.marked_paid",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "previous_status": invoice_status_before,
                "new_status": Invoice.STATUS_PAID,
                "payment_reference": payment_record.payment_reference,
                "manual_bank_reference": payment_record.manual_bank_reference,
                "source": "bank_transfer_confirmation",
            },
            ip_address=get_client_ip(request),
        )
    _send_bank_transfer_success_payment_email(request, payment_record)
    messages.success(
        request,
        f"Bank transfer confirmed for invoice {invoice.invoice_number}.",
    )
    return redirect("invoice-detail", pk=invoice.pk)


@login_required
@role_required(SUPERADMIN, ADMIN)
def bank_transfer_settings(request):
    details = PaymentBankDetails.load()
    if request.method != "POST":
        return render(
            request,
            "payments/bank_transfer_settings.html",
            {"form": PaymentBankDetailsForm(instance=details), "details": details},
        )

    previous_account_number = details.account_number
    form = PaymentBankDetailsForm(request.POST, instance=details)
    if form.is_valid():
        changed_fields = list(form.changed_data)
        if changed_fields:
            settings_obj = form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()

            metadata = {"changed_fields": changed_fields}
            if "account_number" in changed_fields:
                metadata.update(
                    {
                        "account_number_before": mask_bank_account_number(previous_account_number),
                        "account_number_after": mask_bank_account_number(settings_obj.account_number),
                    }
                )
            log_event(
                action="payment.bank_transfer_details.updated",
                user=request.user,
                target_type="payment_bank_details",
                target_id=str(settings_obj.id),
                metadata=metadata,
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Bank transfer details updated.")
        else:
            messages.info(request, "No bank transfer detail changes to save.")
        return redirect("payment-bank-transfer-settings")

    return render(
        request,
        "payments/bank_transfer_settings.html",
        {"form": form, "details": details},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
@require_POST
def refund_invoice_payment(request, pk):
    # Only admin/finance roles can request refunds from this endpoint.
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    # Use the latest Stripe payment record for this invoice.
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
        # Refunds need an existing Stripe payment record.
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
        # Avoid refunding the same payment twice.
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

    # Audit that a refund was requested.
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
        # Ask Stripe to create the refund.
        refund = create_full_refund_for_payment(
            payment_record=payment_record,
            initiated_by=request.user,
        )
    except ImproperlyConfigured as exc:
        # Missing Stripe settings are logged and shown to the user.
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
        # Validation errors, such as refunding a pending payment, come here.
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
        # Unexpected Stripe refund failures are logged with details.
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

    # Refresh from the database because the refund service may have changed statuses.
    refund_status = str(getattr(refund, "status", "") or "").lower()
    payment_record.refresh_from_db()
    invoice.refresh_from_db()
    if refund_status == "succeeded":
        # Refund finished immediately, so email the customer and log success.
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
        # Refund exists but is not final yet, so email/log it as pending or unclear.
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
    # Page Stripe redirects to after checkout succeeds.
    session_id = request.GET.get("session_id", "").strip()
    invoice = None
    payment_record = None
    payment_status = None

    if session_id:
        try:
            # Ask Stripe for the session details.
            session = retrieve_checkout_session(session_id)
        except ImproperlyConfigured as exc:
            # Show a setup error if Stripe is not configured.
            messages.error(request, str(exc))
            session = None
        except Exception:
            # If Stripe cannot be reached, still render the page safely.
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
                amount_total=getattr(session, "amount_total", None),
                currency=getattr(session, "currency", None),
                metadata=metadata,
            )
            if payment_record is None:
                # Fallback lookup by Stripe session ID.
                payment_record = PaymentRecord.objects.select_related("invoice").filter(
                    stripe_checkout_session_id=session_id
                ).first()
            if payment_record is not None:
                invoice = payment_record.invoice
                if payment_record.status == PaymentRecord.STATUS_SUCCEEDED:
                    # Send payment success email when the payment is confirmed.
                    _send_success_payment_email(request, payment_record)

    # Show the success/status page to the user.
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
    # Page Stripe redirects to when checkout is cancelled.
    next_url = request.GET.get("next", "").strip()
    session_id = request.GET.get("session_id", "").strip()
    payment_reference = request.GET.get("payment_reference", "").strip()

    payment_record = None
    if session_id:
        # Try to find the payment by Stripe Checkout session ID.
        payment_record = PaymentRecord.objects.select_related("invoice").filter(
            stripe_checkout_session_id=session_id
        ).first()
    if payment_record is None and payment_reference:
        # If session ID is missing, try the internal payment reference.
        payment_record = PaymentRecord.objects.select_related("invoice").filter(
            payment_reference=payment_reference
        ).first()

    if (
        payment_record is not None
        and payment_record.status
        not in {PaymentRecord.STATUS_SUCCEEDED, PaymentRecord.STATUS_CANCELLED}
    ):
        # Mark unfinished payments as cancelled.
        payment_record.status = PaymentRecord.STATUS_CANCELLED
        payment_record.save(update_fields=["status", "updated_at"])

    if payment_record is not None:
        # Log a cancellation linked to a known payment/invoice.
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
            # Tell the customer the checkout was cancelled before payment.
            _send_failed_payment_email(
                request,
                payment_record,
                failure_reason="Stripe Checkout was cancelled before payment was completed.",
            )
    else:
        # Log the cancellation even if no local payment record was found.
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

    # Show the cancellation page to the user.
    return render(
        request,
        "payments/checkout_cancel.html",
        {"next_url": next_url, "payment_record": payment_record},
    )


@csrf_exempt
@require_POST
def stripe_webhook(request):
    # Endpoint Stripe calls in the background to confirm payment/refund events.
    payload = request.body
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    if not signature:
        # Reject requests that do not include Stripe's signature header.
        return HttpResponseBadRequest("Missing Stripe-Signature header.")

    try:
        # Verify the Stripe signature before trusting the event.
        event = construct_webhook_event(payload=payload, stripe_signature=signature)
    except ValueError:
        # The request body was not valid JSON/event data.
        return HttpResponseBadRequest("Invalid payload.")
    except ImproperlyConfigured as exc:
        # Webhook settings are missing or invalid.
        log_event(
            action="payment.webhook.configuration_error",
            user=None,
            metadata={"reason": str(exc), "path": request.path},
            ip_address=get_client_ip(request),
        )
        return HttpResponse("Stripe webhook is not configured.", status=500)
    except Exception:
        # Signature failed, so the request may not be from Stripe.
        return HttpResponseBadRequest("Invalid webhook signature.")

    # Process the event and update local payment/invoice records.
    event_record, created = process_webhook_event(event)
    if not created:
        # Duplicate events are acknowledged without sending duplicate emails.
        return HttpResponse(status=200)

    if event_record.status == event_record.STATUS_FAILED:
        # Tell Stripe to retry if processing failed.
        return HttpResponse(status=500)

    if event_record.status == event_record.STATUS_PROCESSED and event_record.payment_record_id:
        payment_record = event_record.payment_record
        if event_record.event_type in {WEBHOOK_EVENT_COMPLETED, WEBHOOK_EVENT_ASYNC_SUCCEEDED}:
            # Successful payment webhook sends a success email.
            _send_success_payment_email(request, payment_record)
        elif event_record.event_type == WEBHOOK_EVENT_ASYNC_FAILED:
            # Failed payment webhook sends a failed payment email.
            _send_failed_payment_email(
                request,
                payment_record,
                failure_reason="Stripe reported that the payment failed.",
            )
        elif event_record.event_type == WEBHOOK_EVENT_EXPIRED:
            # Expired checkout webhook sends a failed/cancelled payment email.
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
            # Refund webhooks send either success or failure emails when final.
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
