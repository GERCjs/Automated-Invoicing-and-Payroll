from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from invoicing.exports import generate_invoice_pdf
from invoicing.models import Invoice

from .models import EmailDeliveryLog


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
