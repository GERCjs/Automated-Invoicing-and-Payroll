from django.apps import AppConfig


# This tells Django that the folder named "accounts" is a Django app.
class AccountsConfig(AppConfig):
    # New models in this app use BigAutoField primary keys by default.
    default_auto_field = "django.db.models.BigAutoField"
    name = 'accounts'

    def ready(self):
        # Import signals when Django starts so login/user events are connected.
        from . import signals  # noqa: F401
