from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid

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
    failed_login_attempts = models.PositiveIntegerField(default=0)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.CharField(max_length=255, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suspended_accounts",
        db_constraint=False,
    )
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

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    def suspend(self, *, by=None, reason: str = ""):
        self.suspended_at = timezone.now()
        self.suspended_reason = reason.strip()[:255]
        self.suspended_by = by
        self.save(update_fields=["suspended_at", "suspended_reason", "suspended_by", "updated_at"])
        if self.user.is_active:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

    def unsuspend(self):
        self.suspended_at = None
        self.suspended_reason = ""
        self.suspended_by = None
        self.failed_login_attempts = 0
        self.save(
            update_fields=[
                "suspended_at",
                "suspended_reason",
                "suspended_by",
                "failed_login_attempts",
                "updated_at",
            ]
        )
        if not self.user.is_active:
            self.user.is_active = True
            self.user.save(update_fields=["is_active"])


class LoginSecurityPolicy(models.Model):
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    max_failed_login_attempts = models.PositiveSmallIntegerField(default=5)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_login_security_policies",
        db_constraint=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role"]

    def __str__(self) -> str:
        return f"{self.get_role_display()} ({self.max_failed_login_attempts})"

    @classmethod
    def get_for_role(cls, role: str):
        return cls.objects.get_or_create(role=role)[0]


class EmailVerificationToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Verification token for {self.user.username}"

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    @classmethod
    def issue_for_user(cls, user, validity_hours: int = 48):
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        return cls.objects.create(
            user=user,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(hours=validity_hours),
        )
