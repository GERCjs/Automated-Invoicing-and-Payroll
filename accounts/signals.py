from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.audit import get_client_ip, log_event

from .models import LoginSecurityPolicy, UserRole
from .roles import ADMIN, SUPERADMIN

User = get_user_model()
ADMIN_CONSOLE_GROUP_NAME = "RoleAdminConsole"


@receiver(post_save, sender=User)
def create_or_update_user_role(sender, instance, created, **kwargs):
    if created:
        role = SUPERADMIN if instance.is_superuser else UserRole._meta.get_field("role").default
        UserRole.objects.create(user=instance, role=role)
        return
    role_profile, _ = UserRole.objects.get_or_create(user=instance)
    if instance.is_superuser and role_profile.role != SUPERADMIN:
        role_profile.role = SUPERADMIN
        role_profile.save(update_fields=["role", "updated_at"])


@receiver(post_save, sender=UserRole)
def sync_user_staff_flag_from_role(sender, instance, **kwargs):
    user = instance.user
    expected_superuser = instance.role == SUPERADMIN
    expected_staff = instance.role in {SUPERADMIN, ADMIN}

    fields_to_update = []
    if user.is_superuser != expected_superuser:
        user.is_superuser = expected_superuser
        fields_to_update.append("is_superuser")
    if user.is_staff != expected_staff:
        user.is_staff = expected_staff
        fields_to_update.append("is_staff")
    if fields_to_update:
        user.save(update_fields=fields_to_update)

    admin_group, _ = Group.objects.get_or_create(name=ADMIN_CONSOLE_GROUP_NAME)
    admin_group.permissions.set(Permission.objects.all())

    if instance.role == ADMIN:
        user.groups.add(admin_group)
    else:
        user.groups.remove(admin_group)


@receiver(user_logged_in)
def log_user_logged_in(sender, request, user, **kwargs):
    role_profile = getattr(user, "role_profile", None)
    had_failed_attempts = bool(role_profile and role_profile.failed_login_attempts)
    if had_failed_attempts:
        role_profile.failed_login_attempts = 0
        role_profile.save(update_fields=["failed_login_attempts", "updated_at"])

    log_event(
        action="auth.login",
        user=user,
        metadata={
            "username": user.get_username(),
            "failed_login_attempts_reset": had_failed_attempts,
        },
        ip_address=get_client_ip(request),
    )


@receiver(user_logged_out)
def log_user_logged_out(sender, request, user, **kwargs):
    if user is None:
        return
    log_event(
        action="auth.logout",
        user=user,
        metadata={"username": user.get_username()},
        ip_address=get_client_ip(request),
    )


def _resolve_user_from_failed_credentials(credentials: dict):
    username_field = User.USERNAME_FIELD
    username = (
        credentials.get(username_field)
        or credentials.get("username")
        or credentials.get("email")
        or ""
    )
    username = str(username).strip()
    if not username:
        return None
    return (
        User.objects.select_related("role_profile")
        .filter(Q(**{f"{username_field}__iexact": username}) | Q(email__iexact=username))
        .order_by("id")
        .first()
    )


@receiver(user_login_failed)
def handle_user_login_failed(sender, credentials, request, **kwargs):
    user = _resolve_user_from_failed_credentials(credentials or {})
    ip_address = get_client_ip(request) if request is not None else None
    if user is None:
        return

    role_profile = getattr(user, "role_profile", None)
    if role_profile is None:
        return

    if role_profile.is_suspended:
        log_event(
            action="auth.login.failed",
            user=user,
            metadata={
                "username": user.get_username(),
                "failed_login_attempts": role_profile.failed_login_attempts,
                "account_suspended": True,
            },
            ip_address=ip_address,
        )
        return

    role_profile.failed_login_attempts += 1
    role_profile.save(update_fields=["failed_login_attempts", "updated_at"])
    policy = LoginSecurityPolicy.get_for_role(role_profile.role)
    auto_suspended = role_profile.failed_login_attempts >= policy.max_failed_login_attempts

    log_event(
        action="auth.login.failed",
        user=user,
        metadata={
            "username": user.get_username(),
            "role": role_profile.role,
            "failed_login_attempts": role_profile.failed_login_attempts,
            "max_failed_login_attempts": policy.max_failed_login_attempts,
            "auto_suspended": auto_suspended,
        },
        ip_address=ip_address,
    )

    if auto_suspended:
        role_profile.suspend(
            by=None,
            reason=(
                f"Auto-suspended after {role_profile.failed_login_attempts} failed login attempt(s). "
                f"Threshold for role '{role_profile.role}' is {policy.max_failed_login_attempts}."
            ),
        )
        log_event(
            action="auth.login.auto_suspended",
            user=user,
            target_type="user",
            target_id=str(user.id),
            metadata={
                "username": user.get_username(),
                "role": role_profile.role,
                "failed_login_attempts": role_profile.failed_login_attempts,
                "max_failed_login_attempts": policy.max_failed_login_attempts,
            },
            ip_address=ip_address,
        )
