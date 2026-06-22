from __future__ import annotations

from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from invoicing.services import refresh_overdue_invoices
from invoicing.exports import generate_invoice_pdf
from invoicing.models import Invoice

from .models import EmailDeliveryLog, PaymentReminderSettings

STRIPE_PAYMENT_SUCCESS_EMAIL_TEMPLATE_KEY = "stripe_payment_success_invoice_email_v1"
STRIPE_PAYMENT_FAILED_EMAIL_TEMPLATE_KEY = "stripe_payment_failed_invoice_email_v1"
STRIPE_REFUND_SUCCESS_EMAIL_TEMPLATE_KEY = "stripe_refund_success_invoice_email_v1"
STRIPE_REFUND_FAILED_EMAIL_TEMPLATE_KEY = "stripe_refund_failed_invoice_email_v1"


def _find_existing_sent_payment_email(
    *,
    template_key: str,
    invoice: Invoice,
    payment_record,
) -> EmailDeliveryLog | None:
    sent_logs = EmailDeliveryLog.objects.filter(
        template_key=template_key,
        status=EmailDeliveryLog.STATUS_SENT,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
    ).order_by("-attempted_at")
    for sent_log in sent_logs:
        metadata = sent_log.metadata or {}
        if metadata.get("payment_record_id") == str(payment_record.id):
            return sent_log
    return None


def _build_stripe_payment_email_context(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    payment_outcome: str,
    failure_reason: str = "",
) -> dict:
    amount_due = Decimal("0.00") if payment_outcome == "successful" else _calculate_amount_due(invoice)
    return {
        "invoice": invoice,
        "invoice_items": invoice.items.all(),
        "invoice_status_label": invoice.get_status_display(),
        "customer": invoice.customer,
        "payment_record": payment_record,
        "payment_outcome": payment_outcome,
        "failure_reason": failure_reason,
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "amount_due": amount_due,
        "public_invoice_url": public_invoice_url,
    }


def _calculate_amount_due(invoice: Invoice) -> Decimal:
    if invoice.status == Invoice.STATUS_PAID:
        return Decimal("0.00")
    return invoice.total_amount


def send_invoice_email(
    invoice: Invoice,
    public_invoice_url: str,
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    recipient = (invoice.customer.email or "").strip().lower()
    amount_due = _calculate_amount_due(invoice)
    context = {
        "invoice": invoice,
        "customer": invoice.customer,
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "amount_due": amount_due,
        "public_invoice_url": public_invoice_url,
    }
    subject = render_to_string("invoicing/emails/invoice_email_subject.txt", context).strip()
    text_body = render_to_string("invoicing/emails/invoice_email_body.txt", context)
    html_body = render_to_string("invoicing/emails/invoice_email_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key="invoice_email_v1",
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata={
            "invoice_number": invoice.invoice_number,
            "invoice_status_before_send": invoice.status,
            "customer_name": invoice.customer.name,
            "amount_due": str(amount_due),
            "public_invoice_url": public_invoice_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return False, log

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")

    attachment_added = False
    attachment_error = ""
    try:
        pdf_bytes = generate_invoice_pdf(invoice)
        message.attach(
            filename=f"{invoice.invoice_number}.pdf",
            content=pdf_bytes,
            mimetype="application/pdf",
        )
        attachment_added = True
    except Exception as exc:
        attachment_error = str(exc)

    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return False, log

    with transaction.atomic():
        if invoice.status == Invoice.STATUS_DRAFT:
            invoice.status = Invoice.STATUS_SENT
            invoice.save(update_fields=["status", "updated_at"])
        log.status = EmailDeliveryLog.STATUS_SENT
        log.sent_at = timezone.now()
        metadata = dict(log.metadata or {})
        metadata.update(
            {
                "invoice_status_after_send": invoice.status,
                "pdf_attachment_added": attachment_added,
            }
        )
        if attachment_error:
            metadata["pdf_attachment_error"] = attachment_error
        log.metadata = metadata
        log.save(update_fields=["status", "sent_at", "metadata"])
    return True, log


def send_stripe_payment_success_email(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    existing_log = _find_existing_sent_payment_email(
        template_key=STRIPE_PAYMENT_SUCCESS_EMAIL_TEMPLATE_KEY,
        invoice=invoice,
        payment_record=payment_record,
    )
    if existing_log is not None:
        return True, existing_log

    recipient = (invoice.customer.email or "").strip().lower()
    context = _build_stripe_payment_email_context(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=public_invoice_url,
        payment_outcome="successful",
    )
    subject = render_to_string("payments/emails/stripe_payment_success_subject.txt", context).strip()
    text_body = render_to_string("payments/emails/stripe_payment_success_body.txt", context)
    html_body = render_to_string("payments/emails/stripe_payment_success_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=STRIPE_PAYMENT_SUCCESS_EMAIL_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata={
            "invoice_number": invoice.invoice_number,
            "invoice_status_before_send": invoice.status,
            "customer_name": invoice.customer.name,
            "payment_record_id": str(payment_record.id),
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "successful",
            "payment_record_status": payment_record.status,
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id or "",
            "public_invoice_url": public_invoice_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return False, log

    try:
        pdf_bytes = generate_invoice_pdf(invoice)
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = f"Invoice PDF could not be generated: {exc}"
        metadata = dict(log.metadata or {})
        metadata.update({"pdf_attachment_added": False, "pdf_attachment_error": str(exc)})
        log.metadata = metadata
        log.save(update_fields=["status", "error_message", "metadata"])
        return False, log

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")
    message.attach(
        filename=f"{invoice.invoice_number}.pdf",
        content=pdf_bytes,
        mimetype="application/pdf",
    )

    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return False, log

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    metadata = dict(log.metadata or {})
    metadata.update(
        {
            "invoice_status_after_send": invoice.status,
            "pdf_attachment_added": True,
        }
    )
    log.metadata = metadata
    log.save(update_fields=["status", "sent_at", "metadata"])
    return True, log


def send_stripe_payment_failed_email(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    failure_reason: str = "",
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    existing_log = _find_existing_sent_payment_email(
        template_key=STRIPE_PAYMENT_FAILED_EMAIL_TEMPLATE_KEY,
        invoice=invoice,
        payment_record=payment_record,
    )
    if existing_log is not None:
        return True, existing_log

    recipient = (invoice.customer.email or "").strip().lower()
    context = _build_stripe_payment_email_context(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=public_invoice_url,
        payment_outcome="failed",
        failure_reason=failure_reason or "Stripe Checkout did not complete the payment.",
    )
    subject = render_to_string("payments/emails/stripe_payment_failed_subject.txt", context).strip()
    text_body = render_to_string("payments/emails/stripe_payment_failed_body.txt", context)
    html_body = render_to_string("payments/emails/stripe_payment_failed_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=STRIPE_PAYMENT_FAILED_EMAIL_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata={
            "invoice_number": invoice.invoice_number,
            "invoice_status_before_send": invoice.status,
            "customer_name": invoice.customer.name,
            "payment_record_id": str(payment_record.id),
            "payment_reference": payment_record.payment_reference,
            "payment_outcome": "failed",
            "payment_record_status": payment_record.status,
            "failure_reason": context["failure_reason"],
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id or "",
            "public_invoice_url": public_invoice_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return False, log

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")

    attachment_added = False
    attachment_error = ""
    try:
        pdf_bytes = generate_invoice_pdf(invoice)
        message.attach(
            filename=f"{invoice.invoice_number}.pdf",
            content=pdf_bytes,
            mimetype="application/pdf",
        )
        attachment_added = True
    except Exception as exc:
        attachment_error = str(exc)

    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return False, log

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    metadata = dict(log.metadata or {})
    metadata.update(
        {
            "invoice_status_after_send": invoice.status,
            "pdf_attachment_added": attachment_added,
        }
    )
    if attachment_error:
        metadata["pdf_attachment_error"] = attachment_error
    log.metadata = metadata
    log.save(update_fields=["status", "sent_at", "metadata"])
    return True, log


def _build_stripe_refund_email_context(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    refund_status: str,
    failure_reason: str = "",
) -> dict:
    return {
        "invoice": invoice,
        "invoice_items": invoice.items.all(),
        "invoice_status_label": invoice.get_status_display(),
        "customer": invoice.customer,
        "payment_record": payment_record,
        "refund_status": refund_status,
        "failure_reason": failure_reason,
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "amount_due": _calculate_amount_due(invoice),
        "public_invoice_url": public_invoice_url,
    }


def send_stripe_refund_success_email(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    existing_log = _find_existing_sent_payment_email(
        template_key=STRIPE_REFUND_SUCCESS_EMAIL_TEMPLATE_KEY,
        invoice=invoice,
        payment_record=payment_record,
    )
    if existing_log is not None:
        return True, existing_log

    recipient = (invoice.customer.email or "").strip().lower()
    context = _build_stripe_refund_email_context(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=public_invoice_url,
        refund_status="successful",
    )
    subject = render_to_string("payments/emails/stripe_refund_success_subject.txt", context).strip()
    text_body = render_to_string("payments/emails/stripe_refund_success_body.txt", context)
    html_body = render_to_string("payments/emails/stripe_refund_success_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=STRIPE_REFUND_SUCCESS_EMAIL_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata={
            "invoice_number": invoice.invoice_number,
            "invoice_status_before_send": invoice.status,
            "customer_name": invoice.customer.name,
            "payment_record_id": str(payment_record.id),
            "payment_reference": payment_record.payment_reference,
            "refund_status": "successful",
            "payment_record_status": payment_record.status,
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id or "",
            "public_invoice_url": public_invoice_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return False, log

    try:
        pdf_bytes = generate_invoice_pdf(invoice)
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = f"Invoice PDF could not be generated: {exc}"
        metadata = dict(log.metadata or {})
        metadata.update({"pdf_attachment_added": False, "pdf_attachment_error": str(exc)})
        log.metadata = metadata
        log.save(update_fields=["status", "error_message", "metadata"])
        return False, log

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")
    message.attach(
        filename=f"{invoice.invoice_number}.pdf",
        content=pdf_bytes,
        mimetype="application/pdf",
    )

    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return False, log

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    metadata = dict(log.metadata or {})
    metadata.update(
        {
            "invoice_status_after_send": invoice.status,
            "pdf_attachment_added": True,
        }
    )
    log.metadata = metadata
    log.save(update_fields=["status", "sent_at", "metadata"])
    return True, log


def send_stripe_refund_failed_email(
    *,
    invoice: Invoice,
    payment_record,
    public_invoice_url: str,
    failure_reason: str = "",
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    existing_log = _find_existing_sent_payment_email(
        template_key=STRIPE_REFUND_FAILED_EMAIL_TEMPLATE_KEY,
        invoice=invoice,
        payment_record=payment_record,
    )
    if existing_log is not None:
        return True, existing_log

    recipient = (invoice.customer.email or "").strip().lower()
    context = _build_stripe_refund_email_context(
        invoice=invoice,
        payment_record=payment_record,
        public_invoice_url=public_invoice_url,
        refund_status="failed",
        failure_reason=failure_reason or "Stripe reported that the refund failed.",
    )
    subject = render_to_string("payments/emails/stripe_refund_failed_subject.txt", context).strip()
    text_body = render_to_string("payments/emails/stripe_refund_failed_body.txt", context)
    html_body = render_to_string("payments/emails/stripe_refund_failed_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=STRIPE_REFUND_FAILED_EMAIL_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata={
            "invoice_number": invoice.invoice_number,
            "invoice_status_before_send": invoice.status,
            "customer_name": invoice.customer.name,
            "payment_record_id": str(payment_record.id),
            "payment_reference": payment_record.payment_reference,
            "refund_status": "failed",
            "payment_record_status": payment_record.status,
            "failure_reason": context["failure_reason"],
            "stripe_checkout_session_id": payment_record.stripe_checkout_session_id or "",
            "public_invoice_url": public_invoice_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return False, log

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")

    attachment_added = False
    attachment_error = ""
    try:
        pdf_bytes = generate_invoice_pdf(invoice)
        message.attach(
            filename=f"{invoice.invoice_number}.pdf",
            content=pdf_bytes,
            mimetype="application/pdf",
        )
        attachment_added = True
    except Exception as exc:
        attachment_error = str(exc)

    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return False, log

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    metadata = dict(log.metadata or {})
    metadata.update(
        {
            "invoice_status_after_send": invoice.status,
            "pdf_attachment_added": attachment_added,
        }
    )
    if attachment_error:
        metadata["pdf_attachment_error"] = attachment_error
    log.metadata = metadata
    log.save(update_fields=["status", "sent_at", "metadata"])
    return True, log


REMINDER_TEMPLATE_KEYS = {
    "before_due": "payment_reminder_before_due",
    "due_date": "payment_reminder_due_date",
    "after_due": "payment_reminder_after_due",
    "overdue_repeat": "payment_reminder_overdue_repeat",
}


def get_invoice_reminder_history(invoice: Invoice):
    return EmailDeliveryLog.objects.filter(
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        template_key__startswith="payment_reminder_",
    ).order_by("-attempted_at")


def _build_payment_reminder_message(invoice: Invoice, reminder_type: str, public_invoice_url: str) -> tuple[str, str]:
    reminder_titles = {
        "before_due": f"due in {max((invoice.due_date - timezone.localdate()).days, 0)} day(s)",
        "due_date": "due today",
        "after_due": "overdue",
        "overdue_repeat": "still overdue",
    }
    reminder_label = reminder_titles.get(reminder_type, "payment reminder")
    subject = f"Payment Reminder: {invoice.invoice_number} is {reminder_label}"
    body = (
        f"Dear {invoice.customer.name},\n\n"
        f"This is a payment reminder for invoice {invoice.invoice_number}.\n"
        f"Due date: {invoice.due_date:%Y-%m-%d}\n"
        f"Amount due: {invoice.currency} {invoice.total_amount:.2f}\n\n"
        f"View invoice: {public_invoice_url}\n\n"
        f"If payment has already been made, please disregard this reminder.\n\n"
        f"{settings.COMPANY_NAME}\n"
        f"{settings.COMPANY_EMAIL}"
    )
    return subject, body


def _send_payment_reminder(
    invoice: Invoice,
    reminder_type: str,
    triggered_by=None,
    base_url: str = "",
    simulate: bool = True,
) -> EmailDeliveryLog:
    template_key = REMINDER_TEMPLATE_KEYS[reminder_type]
    recipient = (invoice.customer.email or "").strip().lower()
    base_url = (base_url or "").rstrip("/")
    public_path = reverse("invoice-public-view", args=[invoice.public_view_token])
    public_invoice_url = f"{base_url}{public_path}" if base_url else public_path
    subject, body = _build_payment_reminder_message(invoice, reminder_type, public_invoice_url)

    metadata = {
        "invoice_number": invoice.invoice_number,
        "invoice_status": invoice.status,
        "customer_name": invoice.customer.name,
        "reminder_type": reminder_type,
        "due_date": invoice.due_date.isoformat(),
        "days_past_due": max((timezone.localdate() - invoice.due_date).days, 0),
        "public_invoice_url": public_invoice_url,
        "simulate": simulate,
    }
    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=template_key,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="invoice",
        related_object_id=str(invoice.id),
        triggered_by=triggered_by,
        metadata=metadata,
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Customer email is missing on invoice customer record."
        log.save(update_fields=["status", "error_message"])
        return log

    if simulate:
        log.metadata = {**metadata, "simulation_result": "No email sent. Dry run only."}
        log.save(update_fields=["metadata"])
        return log

    try:
        sent_count = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        return log

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    log.metadata = {**metadata, "simulation_result": "Sent"}
    log.save(update_fields=["status", "sent_at", "metadata"])
    return log


def run_payment_reminder_check(*, triggered_by=None, base_url: str = "", simulate: bool = True) -> dict:
    refresh_overdue_invoices()
    today = timezone.localdate()
    settings_obj = PaymentReminderSettings.load()
    invoice_scope = Invoice.objects.select_related("customer").filter(
        status__in=[Invoice.STATUS_SENT, Invoice.STATUS_VIEWED, Invoice.STATUS_OVERDUE]
    )

    candidates: dict[int, tuple[Invoice, str]] = {}
    skipped_not_due = 0

    def add_candidates(queryset, reminder_type: str):
        for invoice in queryset:
            if invoice.id not in candidates:
                candidates[invoice.id] = (invoice, reminder_type)

    if settings_obj.before_due_reminders_enabled and settings_obj.reminder_days_before_due > 0:
        add_candidates(
            invoice_scope.filter(due_date=today + timedelta(days=settings_obj.reminder_days_before_due)),
            "before_due",
        )
    if settings_obj.due_date_reminders_enabled:
        add_candidates(invoice_scope.filter(due_date=today), "due_date")
    if settings_obj.after_due_reminders_enabled and settings_obj.after_due_days > 0:
        add_candidates(
            invoice_scope.filter(due_date=today - timedelta(days=settings_obj.after_due_days)),
            "after_due",
        )
    if settings_obj.overdue_repeat_enabled and settings_obj.overdue_repeat_days > 0:
        for invoice in invoice_scope.filter(status=Invoice.STATUS_OVERDUE, due_date__lt=today):
            overdue_days = (today - invoice.due_date).days
            last_repeat_log = (
                EmailDeliveryLog.objects.filter(
                    Q(status=EmailDeliveryLog.STATUS_SENT)
                    | Q(status=EmailDeliveryLog.STATUS_PENDING, metadata__simulate=False),
                    related_object_type="invoice",
                    related_object_id=str(invoice.id),
                    template_key=REMINDER_TEMPLATE_KEYS["overdue_repeat"],
                )
                .order_by("-attempted_at")
                .first()
            )
            if last_repeat_log and (today - last_repeat_log.attempted_at.date()).days < settings_obj.overdue_repeat_days:
                skipped_not_due += 1
                continue
            if not last_repeat_log and overdue_days < settings_obj.overdue_repeat_days:
                skipped_not_due += 1
                continue
            if overdue_days > 0:
                if invoice.id not in candidates:
                    candidates[invoice.id] = (invoice, "overdue_repeat")

    real_attempt_today_filter = Q(status=EmailDeliveryLog.STATUS_SENT) | Q(
        status=EmailDeliveryLog.STATUS_PENDING,
        metadata__simulate=False,
    )
    existing_today = set(
        EmailDeliveryLog.objects.filter(
            real_attempt_today_filter,
            related_object_type="invoice",
            template_key__startswith="payment_reminder_",
            attempted_at__date=today,
        ).values_list("related_object_id", "template_key")
    )

    reminder_logs = []
    skipped_already_logged = 0
    for invoice, reminder_type in candidates.values():
        template_key = REMINDER_TEMPLATE_KEYS[reminder_type]
        existing_key = (str(invoice.id), template_key)
        if existing_key in existing_today:
            skipped_already_logged += 1
            continue
        if not simulate and EmailDeliveryLog.objects.filter(
            real_attempt_today_filter,
            related_object_type="invoice",
            related_object_id=str(invoice.id),
            template_key=template_key,
            attempted_at__date=today,
        ).exists():
            skipped_already_logged += 1
            continue
        reminder_logs.append(
            _send_payment_reminder(
                invoice=invoice,
                reminder_type=reminder_type,
                triggered_by=triggered_by,
                base_url=base_url,
                simulate=simulate,
            )
        )
        if not simulate:
            existing_today.add(existing_key)

    sent_count = sum(1 for log in reminder_logs if log.status == EmailDeliveryLog.STATUS_SENT)
    failed_count = sum(1 for log in reminder_logs if log.status == EmailDeliveryLog.STATUS_FAILED)
    simulated_count = sum(1 for log in reminder_logs if log.status == EmailDeliveryLog.STATUS_PENDING)
    return {
        "simulate": simulate,
        "checked_invoices": len(candidates),
        "processed": len(reminder_logs),
        "sent": sent_count,
        "failed": failed_count,
        "simulated": simulated_count,
        "skipped_already_logged_today": skipped_already_logged,
        "skipped_not_due": skipped_not_due,
        "log_ids": [log.id for log in reminder_logs],
    }
