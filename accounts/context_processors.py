from .roles import SUPERADMIN


def user_role(request):
    # This adds the current user's role to every template context.
    if not request.user.is_authenticated:
        return {"current_user_role": None, "current_user_role_label": None}
    # Django superusers are treated as SuperAdmin in the app.
    if request.user.is_superuser:
        return {"current_user_role": SUPERADMIN, "current_user_role_label": "SuperAdmin"}
    # Normal users get their role from the UserRole profile.
    profile = getattr(request.user, "role_profile", None)
    if profile is None:
        return {"current_user_role": None, "current_user_role_label": None}
    return {
        "current_user_role": profile.role,
        "current_user_role_label": profile.get_role_display(),
    }
