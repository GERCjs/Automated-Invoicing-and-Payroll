from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from notifications.models import EmailDeliveryLog


SUPPORT_TICKET_RESOLVED_TEMPLATE_KEY = "support_ticket_resolved_v1"


def send_support_ticket_resolved_email(*, ticket, ticket_url, triggered_by=None):
    recipient = ticket.requester_email.lower()
    context = {
        "ticket": ticket,
        "ticket_url": ticket_url,
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
    }
    subject = render_to_string("support/emails/ticket_resolved_subject.txt", context).strip()
    text_body = render_to_string("support/emails/ticket_resolved_body.txt", context)
    html_body = render_to_string("support/emails/ticket_resolved_body.html", context)

    log = EmailDeliveryLog.objects.create(
        recipient_email=recipient or "missing@example.com",
        subject=subject,
        template_key=SUPPORT_TICKET_RESOLVED_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="support_ticket",
        related_object_id=str(ticket.id),
        triggered_by=triggered_by,
        metadata={
            "ticket_id": str(ticket.id),
            "ticket_subject": ticket.subject,
            "ticket_status": ticket.status,
            "ticket_url": ticket_url,
        },
    )

    if not recipient:
        log.status = EmailDeliveryLog.STATUS_FAILED
        log.error_message = "Requester email is missing on support ticket requester account."
        log.save(update_fields=["status", "error_message"])
        return False, log

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

    log.status = EmailDeliveryLog.STATUS_SENT
    log.sent_at = timezone.now()
    log.save(update_fields=["status", "sent_at"])
    return True, log
