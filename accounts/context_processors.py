def user_role(request):
    if not request.user.is_authenticated:
        return {"current_user_role": None, "current_user_role_label": None}
    if request.user.is_superuser:
        return {"current_user_role": "admin", "current_user_role_label": "Admin"}
    profile = getattr(request.user, "role_profile", None)
    if profile is None:
        return {"current_user_role": None, "current_user_role_label": None}
    return {
        "current_user_role": profile.role,
        "current_user_role_label": profile.get_role_display(),
    }
