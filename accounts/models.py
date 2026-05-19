from django.conf import settings
from django.db import models

from .roles import ADMIN, CUSTOMER, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN


ROLE_CODE_PREFIXES = {
    SUPERADMIN: "SUP",
    ADMIN: "ADM",
    FINANCE: "FIN",
    HR: "HR",
    STAFF: "STF",
    CUSTOMER: "CUS",
}


def generate_code_id(role: str) -> str:
    prefix = ROLE_CODE_PREFIXES.get(role, "USR")
    existing_codes = UserRole.objects.filter(code_id__startswith=f"{prefix}-").values_list("code_id", flat=True)
    max_sequence = 0
    for code in existing_codes:
        try:
            max_sequence = max(max_sequence, int(str(code).rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"{prefix}-{max_sequence + 1:06d}"


class UserRole(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_profile",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=STAFF)
    code_id = models.CharField(max_length=30, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self) -> str:
        return f"{self.user.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        if not self.code_id:
            self.code_id = generate_code_id(self.role)
        else:
            self.code_id = self.code_id.strip().upper()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"code_id"}
        super().save(*args, **kwargs)
