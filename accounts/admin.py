from django.contrib import admin

from .models import UserRole
from .roles import CUSTOMER


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "code_id", "role", "updated_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "code_id")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.role == CUSTOMER:
            readonly_fields.append("role")
        return readonly_fields

    def save_model(self, request, obj, form, change):
        if change and obj.pk:
            original = UserRole.objects.get(pk=obj.pk)
            if original.role == CUSTOMER:
                obj.role = CUSTOMER
        super().save_model(request, obj, form, change)
