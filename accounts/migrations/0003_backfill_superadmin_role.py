from django.conf import settings
from django.db import migrations


def set_superuser_roles(apps, schema_editor):
    # During migration, use historical model versions from apps.get_model.
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app_label, user_model_name)
    UserRole = apps.get_model("accounts", "UserRole")

    superusers = User.objects.filter(is_superuser=True)
    for user in superusers.iterator():
        # Make sure every Django superuser has a matching SuperAdmin role profile.
        role_profile, _ = UserRole.objects.get_or_create(user=user)
        if role_profile.role != "superadmin":
            role_profile.role = "superadmin"
            role_profile.save(update_fields=["role", "updated_at"])


class Migration(migrations.Migration):
    # This migration runs after SuperAdmin became an allowed role.
    dependencies = [
        ("accounts", "0002_alter_userrole_role"),
    ]

    # Run the data-fix function above.
    operations = [
        migrations.RunPython(set_superuser_roles, migrations.RunPython.noop),
    ]
