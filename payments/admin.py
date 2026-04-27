from django.contrib import admin

from .models import PaymentRecord


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "invoice", "provider", "status", "amount", "paid_at")
    list_filter = ("provider", "status", "paid_at")
    search_fields = ("payment_reference", "invoice__invoice_number", "external_transaction_id")
