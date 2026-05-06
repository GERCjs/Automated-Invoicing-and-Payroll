from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.audit import get_client_ip, log_event

from .models import UserRole
from .roles import SUPERADMIN

User = get_user_model()


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


@receiver(user_logged_in)
def log_user_logged_in(sender, request, user, **kwargs):
    log_event(
        action="auth.login",
        user=user,
        metadata={"username": user.get_username()},
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
