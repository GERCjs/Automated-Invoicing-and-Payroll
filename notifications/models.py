import re

from django.conf import settings
from django.db import models


PAYMENT_REMINDER_DEFAULT_SUBJECT_TEMPLATE = "Payment Reminder: {{ invoice_number }} is {{ reminder_status }}"
PAYMENT_REMINDER_DEFAULT_BODY_TEMPLATE = (
    "Dear {{ customer_name }},\n\n"
    "This is a payment reminder for invoice {{ invoice_number }}.\n"
    "Due date: {{ due_date }}\n"
    "Amount due: {{ currency }} {{ amount_due }}\n\n"
    "View invoice: {{ invoice_link }}\n\n"
    "If payment has already been made, please disregard this reminder.\n\n"
    "{{ company_name }}\n"
    "{{ company_email }}"
)
PAYMENT_REMINDER_TEMPLATE_FIELDS = [
    "customer_name",
    "invoice_number",
    "reminder_status",
    "due_date",
    "currency",
    "amount_due",
    "invoice_link",
    "company_name",
    "company_email",
]
_PAYMENT_REMINDER_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def get_unknown_payment_reminder_template_fields(template: str):
    placeholders = set(_PAYMENT_REMINDER_PLACEHOLDER_RE.findall(template or ""))
    return sorted(placeholders.difference(PAYMENT_REMINDER_TEMPLATE_FIELDS))


def render_payment_reminder_template(template: str, values: dict):
    def replace_placeholder(match):
        return str(values.get(match.group(1), ""))

    return _PAYMENT_REMINDER_PLACEHOLDER_RE.sub(replace_placeholder, template or "")


class EmailDeliveryLog(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    template_key = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    related_object_type = models.CharField(max_length=100, blank=True)
    related_object_id = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs_triggered",
    )

    class Meta:
        db_table = "email_log"
        ordering = ["-attempted_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["recipient_email"]),
            models.Index(fields=["attempted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_email} - {self.status}"


class PaymentReminderSettings(models.Model):
    before_due_reminders_enabled = models.BooleanField(default=True)
    reminder_days_before_due = models.PositiveSmallIntegerField(default=7)
    due_date_reminders_enabled = models.BooleanField(default=True)
    after_due_reminders_enabled = models.BooleanField(default=True)
    after_due_days = models.PositiveSmallIntegerField(default=1)
    overdue_repeat_enabled = models.BooleanField(default=True)
    overdue_reminders_enabled = models.BooleanField(default=True)
    overdue_repeat_days = models.PositiveSmallIntegerField(default=7)
    mass_email_enabled = models.BooleanField(default=True)
    reminder_subject_template = models.CharField(
        max_length=255,
        blank=True,
        default=PAYMENT_REMINDER_DEFAULT_SUBJECT_TEMPLATE,
    )
    reminder_body_template = models.TextField(
        blank=True,
        default=PAYMENT_REMINDER_DEFAULT_BODY_TEMPLATE,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_reminder_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment reminder settings"
        verbose_name_plural = "Payment reminder settings"

    def __str__(self) -> str:
        return "Payment reminder settings"

    @classmethod
    def load(cls):
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj
