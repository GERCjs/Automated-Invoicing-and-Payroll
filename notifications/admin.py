from django.contrib import admin

from .models import EmailDeliveryLog


@admin.register(EmailDeliveryLog)
class EmailDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ("recipient_email", "subject", "status", "attempted_at", "sent_at")
    list_filter = ("status", "attempted_at", "sent_at")
    search_fields = ("recipient_email", "subject", "template_key")
