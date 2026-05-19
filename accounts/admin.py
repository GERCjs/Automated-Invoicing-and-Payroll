from django import forms
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import UserRole
from .roles import CUSTOMER

User = get_user_model()


class PasswordResetOnlyWidget(forms.Widget):
    def render(self, name, value, attrs=None, renderer=None):
        usable_password = value and not str(value).startswith(UNUSABLE_PASSWORD_PREFIX)
        button_label = _("Reset password") if usable_password else _("Set password")
        return format_html(
            '<div><p><a role="button" class="button" href="../password/">{}</a></p></div>',
            button_label,
        )

    def id_for_label(self, id_):
        return None


class UserChangeFormWithoutPasswordHash(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password" in self.fields:
            self.fields["password"].widget = PasswordResetOnlyWidget()


try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeFormWithoutPasswordHash


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
