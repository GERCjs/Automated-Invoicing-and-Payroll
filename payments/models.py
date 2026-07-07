from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# A PaymentBankDetails row stores the current company bank-transfer instructions.
class PaymentBankDetails(models.Model):
    account_name = models.CharField(max_length=255, blank=True, default="")
    bank_name = models.CharField(max_length=100, default="")
    account_number = models.CharField(max_length=64, default="")
    paynow_id = models.CharField(max_length=100, blank=True, default="")
    bic = models.CharField(max_length=50, blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_bank_details_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_bank_details"
        verbose_name = "Payment bank details"
        verbose_name_plural = "Payment bank details"

    def __str__(self) -> str:
        return "Payment bank details"

    @classmethod
    def load(cls):
        details, _ = cls.objects.get_or_create(pk=1)
        return details

    def is_complete(self) -> bool:
        return bool(self.bank_name.strip() and self.account_number.strip())

    def as_display_dict(self) -> dict[str, str]:
        return {
            "account_name": self.account_name.strip(),
            "bank_name": self.bank_name.strip(),
            "account_number": self.account_number.strip(),
            "paynow_id": self.paynow_id.strip(),
            "bic": self.bic.strip(),
            "instructions": self.instructions.strip(),
        }


# A PaymentRecord stores one payment attempt or payment result for an invoice.
class PaymentRecord(models.Model):
    # "manual" means the payment was recorded by a person/system outside Stripe.
    PROVIDER_MANUAL = "manual"
    PROVIDER_STRIPE = "stripe"
    # These are the allowed values for the provider field.
    PROVIDER_CHOICES = [
        (PROVIDER_MANUAL, "Manual"),
        (PROVIDER_STRIPE, "Stripe"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    invoice = models.ForeignKey(
        "invoicing.Invoice",
        # PROTECT means an invoice cannot be deleted while payments still point to it.
        on_delete=models.PROTECT,
        # This lets invoice.payment_records find all payments for that invoice.
        related_name="payment_records",
    )
    # Unique internal reference, for example PAY-ABC123.
    payment_reference = models.CharField(max_length=100, unique=True)
    # Tells whether the payment was manual or Stripe.
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MANUAL)
    # Tracks the current payment state, such as pending, succeeded, or failed.
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # Amount paid or attempted. The validator prevents negative amounts.
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    # Three-letter currency code, such as SGD or USD.
    currency = models.CharField(max_length=3, default="SGD")
    # Time when the payment succeeded. It stays blank until payment is confirmed.
    paid_at = models.DateTimeField(null=True, blank=True)
    # External ID from the payment provider, such as a Stripe payment intent ID.
    external_transaction_id = models.CharField(max_length=255, blank=True)
    # Stripe Checkout session ID. Unique so one Stripe session maps to one record.
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    # User who started or recorded the payment, if known.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL keeps the payment record if the user account is deleted.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_records_created",
    )
    # Automatically set when the payment record is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically updated whenever the payment record is saved.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Use a simple database table name instead of the default payments_paymentrecord.
        db_table = "payment"
        # Newest payment records appear first by default.
        ordering = ["-created_at"]
        # Indexes make common searches faster.
        indexes = [
            models.Index(fields=["payment_reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["paid_at"]),
        ]

    def __str__(self) -> str:
        # This is how the object is shown as text in admin/debug output.
        return self.payment_reference


# A StripeWebhookEvent stores one event sent by Stripe to this application.
class StripeWebhookEvent(models.Model):
    # The event was received but may not be processed yet.
    STATUS_RECEIVED = "received"
    # The event was handled successfully.
    STATUS_PROCESSED = "processed"
    # The event was valid, but not useful for this app.
    STATUS_IGNORED = "ignored"
    # The event failed while being processed.
    STATUS_FAILED = "failed"
    # These are the allowed values for the webhook event status field.
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_IGNORED, "Ignored"),
        (STATUS_FAILED, "Failed"),
    ]

    # Stripe's unique event ID. This prevents processing the same webhook twice.
    event_id = models.CharField(max_length=255, unique=True)
    # Type of Stripe event, such as checkout.session.completed.
    event_type = models.CharField(max_length=255)
    # Processing state for this webhook event.
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RECEIVED)
    # Full webhook data from Stripe, stored for debugging and audit history.
    payload = models.JSONField(default=dict, blank=True)
    # Error details if processing fails or the event is ignored.
    error_message = models.TextField(blank=True)
    # Payment record linked to this webhook, if one was found.
    payment_record = models.ForeignKey(
        PaymentRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )
    # Invoice linked to this webhook, if one was found.
    invoice = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stripe_webhook_events",
    )
    # Time when this webhook finished processing.
    processed_at = models.DateTimeField(null=True, blank=True)
    # Automatically set when the webhook record is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically updated whenever the webhook record is saved.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Newest webhook events appear first by default.
        ordering = ["-created_at"]
        # Indexes make filtering and reporting faster.
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["processed_at"]),
        ]

    def __str__(self) -> str:
        # This is how the object is shown as text in admin/debug output.
        return f"{self.event_type} ({self.event_id})"
