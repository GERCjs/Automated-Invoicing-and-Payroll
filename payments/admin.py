from django.contrib import admin

from .models import PaymentBankDetails, PaymentRecord, StripeWebhookEvent


@admin.register(PaymentBankDetails)
class PaymentBankDetailsAdmin(admin.ModelAdmin):
    list_display = ("bank_name", "account_name", "updated_by", "updated_at")
    readonly_fields = ("updated_at",)


# This makes PaymentRecord visible and searchable in the Django admin page.
@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    # These columns are shown in the payment list screen.
    list_display = (
        "payment_reference",
        "invoice",
        "provider",
        "status",
        "amount",
        "paid_at",
        "manual_customer_submitted_at",
        "manual_confirmed_by",
    )
    # These fields can be used as sidebar filters.
    list_filter = ("provider", "status", "paid_at", "manual_customer_submitted_at", "manual_confirmed_at")
    # These fields can be searched from the admin search box.
    search_fields = (
        "payment_reference",
        "invoice__invoice_number",
        "external_transaction_id",
        "manual_customer_bank_reference",
        "manual_bank_reference",
    )


# This makes Stripe webhook event records visible in the Django admin page.
@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    # These columns are shown in the webhook event list screen.
    list_display = ("event_id", "event_type", "status", "invoice", "payment_record", "processed_at")
    # These fields can be used as sidebar filters.
    list_filter = ("event_type", "status", "processed_at")
    # These fields can be searched from the admin search box.
    search_fields = ("event_id", "event_type", "invoice__invoice_number", "payment_record__payment_reference")
