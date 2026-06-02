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
    # Choose a prefix based on role, such as ADM for admin or STF for staff.
    prefix = ROLE_CODE_PREFIXES.get(role, "USR")
    # Find existing code IDs for this role so the next number can be generated.
    existing_codes = UserRole.objects.filter(code_id__startswith=f"{prefix}-").values_list("code_id", flat=True)
    max_sequence = 0
    for code in existing_codes:
        try:
            # Read the number at the end of a code like STF-000001.
            max_sequence = max(max_sequence, int(str(code).rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    # Return the next code, padded with zeros.
    return f"{prefix}-{max_sequence + 1:06d}"


# UserRole stores extra account information that Django's built-in User model does not have.
class UserRole(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        # Delete the role profile if the user is deleted.
        on_delete=models.CASCADE,
        # This lets code use user.role_profile to get this object.
        related_name="role_profile",
    )
    # The user's app role, such as admin, finance, staff, or customer.
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=STAFF)
    # Human-readable internal ID, such as STF-000001.
    code_id = models.CharField(max_length=30, unique=True, blank=True)
    # Counts failed login attempts for security lockout.
    failed_login_attempts = models.PositiveIntegerField(default=0)
    # If filled, the account is suspended.
    suspended_at = models.DateTimeField(null=True, blank=True)
    # Short reason explaining why the account was suspended.
    suspended_reason = models.CharField(max_length=255, blank=True)
    # Admin user who suspended the account, if any.
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suspended_accounts",
        db_constraint=False,
    )
    # Automatically set when the role profile is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically updated whenever the role profile is saved.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Sort role profiles by username by default.
        ordering = ["user__username"]

    def __str__(self) -> str:
        # This is how the object is displayed in admin/debug output.
        return f"{self.user.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        # Automatically create a code ID if one was not entered.
        if not self.code_id:
            self.code_id = generate_code_id(self.role)
        else:
            # Keep manually entered code IDs clean and uppercase.
            self.code_id = self.code_id.strip().upper()
        if kwargs.get("update_fields") is not None:
            # Make sure code_id is saved if this method changed it.
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"code_id"}
        super().save(*args, **kwargs)

    @property
    def is_suspended(self) -> bool:
        # A user is suspended when suspended_at has a timestamp.
        return self.suspended_at is not None

    def suspend(self, *, by=None, reason: str = ""):
        # Mark the role profile as suspended.
        self.suspended_at = timezone.now()
        self.suspended_reason = reason.strip()[:255]
        self.suspended_by = by
        self.save(update_fields=["suspended_at", "suspended_reason", "suspended_by", "updated_at"])
        # Also disable the Django user so they cannot log in.
        if self.user.is_active:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

    def unsuspend(self):
        # Clear suspension fields and reset failed login attempts.
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
        # Reactivate the Django user so they can log in again.
        if not self.user.is_active:
            self.user.is_active = True
            self.user.save(update_fields=["is_active"])


# LoginSecurityPolicy stores how many failed logins each role can have before suspension.
class LoginSecurityPolicy(models.Model):
    # One policy row per role.
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    # Failed login limit before auto-suspension.
    max_failed_login_attempts = models.PositiveSmallIntegerField(default=5)
    # Admin user who last updated this policy.
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_login_security_policies",
        db_constraint=False,
    )
    # Automatically set when the policy is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically updated whenever the policy is saved.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Sort policy rows by role by default.
        ordering = ["role"]

    def __str__(self) -> str:
        # Show role and attempt limit in admin/debug output.
        return f"{self.get_role_display()} ({self.max_failed_login_attempts})"

    @classmethod
    def get_for_role(cls, role: str):
        # Return the policy for a role, creating a default one if missing.
        return cls.objects.get_or_create(role=role)[0]


# EmailVerificationToken stores one email verification link for a user.
class EmailVerificationToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Delete verification tokens if the user is deleted.
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    # Random token used in the verification URL.
    token = models.CharField(max_length=64, unique=True, db_index=True)
    # The link stops working after this time.
    expires_at = models.DateTimeField()
    # If filled, the token has already been used.
    used_at = models.DateTimeField(null=True, blank=True)
    # Automatically set when the token is created.
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Newest tokens appear first by default.
        ordering = ["-created_at"]
        # Indexes make token lookup and expiry checks faster.
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        # This is how the object is displayed in admin/debug output.
        return f"Verification token for {self.user.username}"

    @property
    def is_valid(self) -> bool:
        # A token is valid only when it is unused and not expired.
        return self.used_at is None and self.expires_at > timezone.now()

    @classmethod
    def issue_for_user(cls, user, validity_hours: int = 48):
        # Expire older unused tokens so only the newest link should be used.
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        # Create and return a new token.
        return cls.objects.create(
            user=user,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(hours=validity_hours),
        )
