# Generated for web-editable bank transfer details.

import os

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _create_payment_bank_details_table(apps, schema_editor):
    table_name = "payment_bank_details"
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
                `account_name` varchar(255) NOT NULL DEFAULT '',
                `bank_name` varchar(100) NOT NULL DEFAULT '',
                `account_number` varchar(64) NOT NULL DEFAULT '',
                `paynow_id` varchar(100) NOT NULL DEFAULT '',
                `bic` varchar(50) NOT NULL DEFAULT '',
                `instructions` longtext NOT NULL,
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
            "account_name" varchar(255) NOT NULL DEFAULT '',
            "bank_name" varchar(100) NOT NULL DEFAULT '',
            "account_number" varchar(64) NOT NULL DEFAULT '',
            "paynow_id" varchar(100) NOT NULL DEFAULT '',
            "bic" varchar(50) NOT NULL DEFAULT '',
            "instructions" text NOT NULL,
            "updated_at" datetime NOT NULL,
            "updated_by_id" integer NULL
                REFERENCES "{user_table}" ("id") DEFERRABLE INITIALLY DEFERRED
        );
        """
    )
    schema_editor.execute(
        f'CREATE INDEX "{table_name}_updated_by_id_idx" ON "{table_name}" ("updated_by_id");'
    )


def seed_payment_bank_details(apps, schema_editor):
    PaymentBankDetails = apps.get_model("payments", "PaymentBankDetails")
    PaymentBankDetails.objects.update_or_create(
        pk=1,
        defaults={
            "account_name": (os.getenv("BANK_TRANSFER_ACCOUNT_NAME") or "").strip(),
            "bank_name": (os.getenv("BANK_TRANSFER_BANK_NAME") or "").strip(),
            "account_number": (os.getenv("BANK_TRANSFER_ACCOUNT_NUMBER") or "").strip(),
            "paynow_id": (os.getenv("BANK_TRANSFER_PAYNOW_ID") or "").strip(),
            "bic": (os.getenv("BANK_TRANSFER_BIC") or "").strip(),
            "instructions": (os.getenv("BANK_TRANSFER_INSTRUCTIONS") or "").strip(),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_rename_payments_pa_payment_c92984_idx_payment_payment_42bbcf_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_create_payment_bank_details_table, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PaymentBankDetails",
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
                        ("account_name", models.CharField(blank=True, default="", max_length=255)),
                        ("bank_name", models.CharField(default="", max_length=100)),
                        ("account_number", models.CharField(default="", max_length=64)),
                        ("paynow_id", models.CharField(blank=True, default="", max_length=100)),
                        ("bic", models.CharField(blank=True, default="", max_length=50)),
                        ("instructions", models.TextField(blank=True, default="")),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "updated_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="payment_bank_details_updates",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Payment bank details",
                        "verbose_name_plural": "Payment bank details",
                        "db_table": "payment_bank_details",
                    },
                ),
            ],
        ),
        migrations.RunPython(seed_payment_bank_details, migrations.RunPython.noop),
    ]
