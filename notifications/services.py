from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from invoicing.models import Invoice

from .models import EmailDeliveryLog


def send_invoice_email(
    invoice: Invoice,
    public_invoice_url: str,
    triggered_by=None,
) -> tuple[bool, EmailDeliveryLog]:
    context = {
        "invoice": invoice,
        "customer": invoice.customer,
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "public_invoice_url": public_invoice_url,
    }
    subject = render_to_string("invoicing/emails/invoice_email_subject.txt", context).strip()
    text_body = render_to_string("invoicing/emails/invoice_email_body.txt", context)
    html_body = render_to_string("invoicing/emails/invoice_email_body.html", context)
    recipient = invoice.customer.email

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
            "public_invoice_url": public_invoice_url,
        },
    )

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")

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
        log.save(update_fields=["status", "sent_at"])
    return True, log
