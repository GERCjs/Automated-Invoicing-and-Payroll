from functools import wraps

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from core.audit import get_client_ip, log_event
from .roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN


ROLE_LANDING_ROUTE_NAMES = {
    SUPERADMIN: "dashboard",
    ADMIN: "dashboard",
    FINANCE: "invoice-dashboard",
    HR: "payroll-dashboard",
    STAFF: "my-payslips",
    CUSTOMER: "customer-invoice-dashboard",
}


def get_user_role(user):
    # Return None when the user is not logged in.
    if not user.is_authenticated:
        return None
    # Django superusers are treated as SuperAdmin in this project.
    if user.is_superuser:
        return SUPERADMIN
    # Normal users store their app role in user.role_profile.
    profile = getattr(user, "role_profile", None)
    if profile is None:
        return None
    return profile.role


def user_has_role(user, allowed_roles):
    # Check whether a logged-in user has one of the allowed roles.
    if not user.is_authenticated:
        return False
    role = get_user_role(user)
    return role in set(allowed_roles)


def get_role_landing_route_name(user):
    # Return the most useful landing page for the current user's role.
    return ROLE_LANDING_ROUTE_NAMES.get(get_user_role(user), "dashboard")


def role_required(*allowed_roles):
    # This decorator protects function-based views by role.
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not user_has_role(request.user, allowed_roles):
                # Save an audit log whenever someone tries to access a forbidden page.
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
            # If the role is allowed, run the original view function.
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator


class RoleRequiredMixin(UserPassesTestMixin):
    # This mixin protects class-based views by role.
    allowed_roles = ()

    def test_func(self):
        # Django calls this to decide whether access is allowed.
        return user_has_role(self.request.user, self.allowed_roles)

    def handle_no_permission(self):
        # Show a permission error if the user does not have the right role.
        raise PermissionDenied("You do not have permission to access this page.")
