from django.contrib import admin

from .models import PaymentRecord, StripeWebhookEvent


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "invoice", "provider", "status", "amount", "paid_at")
    list_filter = ("provider", "status", "paid_at")
    search_fields = ("payment_reference", "invoice__invoice_number", "external_transaction_id")


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event_type", "status", "invoice", "payment_record", "processed_at")
    list_filter = ("event_type", "status", "processed_at")
    search_fields = ("event_id", "event_type", "invoice__invoice_number", "payment_record__payment_reference")
