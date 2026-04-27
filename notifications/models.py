from django.conf import settings
from django.db import models


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
        ordering = ["-attempted_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["recipient_email"]),
            models.Index(fields=["attempted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_email} - {self.status}"
