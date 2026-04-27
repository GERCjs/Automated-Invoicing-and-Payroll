from django.conf import settings
from django.db import models
from django.db.models import F, Q


class ImportJob(models.Model):
    MODULE_INVOICING = "invoicing"
    MODULE_PAYROLL = "payroll"
    MODULE_CHOICES = [
        (MODULE_INVOICING, "Invoicing"),
        (MODULE_PAYROLL, "Payroll"),
    ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_COMPLETED_WITH_ERRORS, "Completed With Errors"),
        (STATUS_FAILED, "Failed"),
    ]

    module = models.CharField(max_length=20, choices=MODULE_CHOICES)
    source_file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    saved_rows = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_jobs_started",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_rows__gte=0),
                name="import_job_total_rows_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(valid_rows__gte=0),
                name="import_job_valid_rows_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(invalid_rows__gte=0),
                name="import_job_invalid_rows_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(saved_rows__gte=0),
                name="import_job_saved_rows_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(valid_rows__lte=F("total_rows")),
                name="import_job_valid_rows_lte_total_rows",
            ),
            models.CheckConstraint(
                condition=Q(invalid_rows__lte=F("total_rows")),
                name="import_job_invalid_rows_lte_total_rows",
            ),
            models.CheckConstraint(
                condition=Q(saved_rows__lte=F("valid_rows")),
                name="import_job_saved_rows_lte_valid_rows",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.module} - {self.source_file_name}"


class ImportRowError(models.Model):
    import_job = models.ForeignKey(
        ImportJob,
        on_delete=models.CASCADE,
        related_name="row_errors",
    )
    row_number = models.PositiveIntegerField()
    field_name = models.CharField(max_length=100, blank=True)
    error_message = models.TextField()
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]
        indexes = [
            models.Index(fields=["import_job", "row_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.import_job_id} - row {self.row_number}"
