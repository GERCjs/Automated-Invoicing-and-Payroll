from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "user", "target_type", "target_id")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("action", "target_type", "target_id", "user__username")
    readonly_fields = ("created_at",)
