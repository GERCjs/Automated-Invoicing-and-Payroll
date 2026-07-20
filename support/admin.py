from django.contrib import admin

from .models import SupportTicket, SupportTicketSettings


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "category", "status", "priority", "created_by", "assigned_to", "created_at")
    list_filter = ("category", "status", "priority")
    search_fields = ("subject", "message", "related_reference", "created_by__username", "assigned_to__username")


@admin.register(SupportTicketSettings)
class SupportTicketSettingsAdmin(admin.ModelAdmin):
    list_display = ("response_target_days", "updated_by", "updated_at")
