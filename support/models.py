from django.conf import settings
from django.db import models
from django.utils import timezone


class SupportTicket(models.Model):
    ASSIGNED_ROLE_ADMIN = "admin"
    ASSIGNED_ROLE_FINANCE = "finance"
    ASSIGNED_ROLE_PAYROLL = "hr"
    ASSIGNED_ROLE_CHOICES = [
        (ASSIGNED_ROLE_FINANCE, "Finance"),
        (ASSIGNED_ROLE_PAYROLL, "Payroll"),
        (ASSIGNED_ROLE_ADMIN, "Admin"),
    ]

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
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_RESOLVED, "Resolved"),
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
    assigned_role = models.CharField(max_length=20, choices=ASSIGNED_ROLE_CHOICES, blank=True)
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

    @property
    def is_resolved(self):
        return self.status == self.STATUS_RESOLVED

    @property
    def unresolved_age_days(self):
        if self.is_resolved:
            return 0
        created_date = timezone.localtime(self.created_at).date()
        return max((timezone.localdate() - created_date).days, 0)

    @property
    def is_sla_breached(self):
        return not self.is_resolved and self.unresolved_age_days >= settings.SUPPORT_TICKET_SLA_DAYS

    @property
    def assigned_display(self):
        if self.assigned_role:
            return self.get_assigned_role_display()
        if self.assigned_to_id:
            return self.assigned_to.username
        return "Unassigned"

    @property
    def requester_name(self):
        if self.created_by is None:
            return "Unknown user"
        full_name = self.created_by.get_full_name().strip()
        return full_name or self.created_by.username

    @property
    def requester_email(self):
        if self.created_by is None:
            return ""
        return (self.created_by.email or "").strip()

    @property
    def requester_role_display(self):
        if self.created_by is None:
            return "Unknown role"
        role_profile = getattr(self.created_by, "role_profile", None)
        if role_profile is None:
            return "Unknown role"
        return role_profile.get_role_display()

    def mark_resolution_timestamp(self):
        if self.status == self.STATUS_RESOLVED and self.resolved_at is None:
            self.resolved_at = timezone.now()
        if self.status != self.STATUS_RESOLVED:
            self.resolved_at = None
