from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # Map Django auth models to the existing renamed MySQL tables.
        from django.contrib.auth.models import Group, Permission, User

        User._meta.db_table = "user"
        Group._meta.db_table = "user_group"
        Permission._meta.db_table = "user_permission"

        User.groups.through._meta.db_table = "user_account_groups"
        User.user_permissions.through._meta.db_table = "user_account_permissions"
        Group.permissions.through._meta.db_table = "user_group_permissions"
