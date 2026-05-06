from functools import wraps

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from core.audit import get_client_ip, log_event
from .roles import SUPERADMIN


def get_user_role(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return SUPERADMIN
    profile = getattr(user, "role_profile", None)
    if profile is None:
        return None
    return profile.role


def user_has_role(user, allowed_roles):
    if not user.is_authenticated:
        return False
    role = get_user_role(user)
    return role in set(allowed_roles)


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not user_has_role(request.user, allowed_roles):
                log_event(
                    action="auth.permission_denied",
                    user=request.user if request.user.is_authenticated else None,
                    target_type="view",
                    target_id=view_func.__name__,
                    metadata={
                        "path": request.path,
                        "allowed_roles": list(allowed_roles),
                    },
                    ip_address=get_client_ip(request),
                )
                raise PermissionDenied("You do not have permission to access this page.")
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator


class RoleRequiredMixin(UserPassesTestMixin):
    allowed_roles = ()

    def test_func(self):
        return user_has_role(self.request.user, self.allowed_roles)

    def handle_no_permission(self):
        raise PermissionDenied("You do not have permission to access this page.")
