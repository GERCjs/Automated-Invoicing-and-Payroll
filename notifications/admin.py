from django.contrib import admin

from .models import EmailDeliveryLog, PaymentReminderSettings


@admin.register(EmailDeliveryLog)
class EmailDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ("recipient_email", "subject", "status", "attempted_at", "sent_at")
    list_filter = ("status", "attempted_at", "sent_at")
    search_fields = ("recipient_email", "subject", "template_key")


@admin.register(PaymentReminderSettings)
class PaymentReminderSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "before_due_reminders_enabled",
        "reminder_days_before_due",
        "due_date_reminders_enabled",
        "after_due_reminders_enabled",
        "after_due_days",
        "overdue_repeat_enabled",
        "overdue_reminders_enabled",
        "overdue_repeat_days",
        "mass_email_enabled",
        "updated_by",
        "updated_at",
    )
