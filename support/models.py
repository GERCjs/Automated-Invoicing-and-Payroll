from django.conf import settings
from django.db import models
from django.utils import timezone


class SupportTicket(models.Model):
    CATEGORY_INVOICE = "invoice"
    CATEGORY_PAYMENT = "payment"
    CATEGORY_PAYROLL = "payroll"
    CATEGORY_ACCOUNT = "account"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = [
        (CATEGORY_INVOICE, "Invoice"),
        (CATEGORY_PAYMENT, "Payment"),
        (CATEGORY_PAYROLL, "Payroll"),
        (CATEGORY_ACCOUNT, "Account"),
        (CATEGORY_OTHER, "Other"),
    ]

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CLOSED, "Closed"),
    ]

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_URGENT, "Urgent"),
    ]

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    related_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional invoice, payment, or payroll reference.",
    )
    resolution_note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets_created",
        db_constraint=False,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets_assigned",
        db_constraint=False,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_ticket"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_category_display()} - {self.subject}"

    def mark_resolution_timestamp(self):
        if self.status in {self.STATUS_RESOLVED, self.STATUS_CLOSED} and self.resolved_at is None:
            self.resolved_at = timezone.now()
        if self.status not in {self.STATUS_RESOLVED, self.STATUS_CLOSED}:
            self.resolved_at = None
