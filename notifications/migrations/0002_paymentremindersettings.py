from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_payment_reminder_settings_table(apps, schema_editor):
    table_name = "notifications_paymentremindersettings"
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if table_name in existing_tables:
        return

    user_table = "user" if "user" in existing_tables else "auth_user"
    vendor = schema_editor.connection.vendor

    if vendor == "mysql":
        schema_editor.execute(
            f"""
            CREATE TABLE `{table_name}` (
                `id` bigint NOT NULL AUTO_INCREMENT,
                `reminder_days_before_due` smallint unsigned NOT NULL DEFAULT 7,
                `overdue_reminders_enabled` bool NOT NULL DEFAULT 1,
                `overdue_repeat_days` smallint unsigned NOT NULL DEFAULT 7,
                `mass_email_enabled` bool NOT NULL DEFAULT 1,
                `updated_at` datetime(6) NOT NULL,
                `updated_by_id` int NULL,
                PRIMARY KEY (`id`),
                KEY `{table_name}_updated_by_id_idx` (`updated_by_id`),
                CONSTRAINT `{table_name}_updated_by_fk`
                    FOREIGN KEY (`updated_by_id`) REFERENCES `{user_table}` (`id`)
            );
            """
        )
        return

    schema_editor.execute(
        f"""
        CREATE TABLE "{table_name}" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "reminder_days_before_due" smallint unsigned NOT NULL DEFAULT 7,
            "overdue_reminders_enabled" bool NOT NULL DEFAULT 1,
            "overdue_repeat_days" smallint unsigned NOT NULL DEFAULT 7,
            "mass_email_enabled" bool NOT NULL DEFAULT 1,
            "updated_at" datetime NOT NULL,
            "updated_by_id" integer NULL
                REFERENCES "{user_table}" ("id") DEFERRABLE INITIALLY DEFERRED
        );
        """
    )
    schema_editor.execute(
        f'CREATE INDEX "{table_name}_updated_by_id_idx" ON "{table_name}" ("updated_by_id");'
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(create_payment_reminder_settings_table, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PaymentReminderSettings",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("reminder_days_before_due", models.PositiveSmallIntegerField(default=7)),
                        ("overdue_reminders_enabled", models.BooleanField(default=True)),
                        ("overdue_repeat_days", models.PositiveSmallIntegerField(default=7)),
                        ("mass_email_enabled", models.BooleanField(default=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "updated_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="payment_reminder_settings_updates",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Payment reminder settings",
                        "verbose_name_plural": "Payment reminder settings",
                    },
                ),
            ],
        ),
    ]
