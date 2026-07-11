# Generated for customer-submitted manual bank-transfer notices.

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def add_manual_customer_notice_fields_if_missing(apps, schema_editor):
    PaymentRecord = apps.get_model("payments", "PaymentRecord")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    UserModel = apps.get_model(user_app_label, user_model_name)
    table_name = PaymentRecord._meta.db_table
    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(),
            table_name,
        )
    }

    fields = [
        (
            "manual_customer_amount",
            models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        ("manual_customer_transfer_date", models.DateField(blank=True, null=True)),
        ("manual_customer_bank_reference", models.CharField(blank=True, default="", max_length=100)),
        ("manual_customer_notes", models.TextField(blank=True, default="")),
        (
            "manual_customer_proof",
            models.FileField(blank=True, null=True, upload_to="payment_proofs/"),
        ),
        (
            "manual_customer_submitted_by",
            models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="manual_payment_notices_submitted",
                to=UserModel,
            ),
        ),
        ("manual_customer_submitted_at", models.DateTimeField(blank=True, null=True)),
    ]

    for field_name, field in fields:
        field.set_attributes_from_name(field_name)
        field.model = PaymentRecord
        if field.column not in existing_columns:
            schema_editor.add_field(PaymentRecord, field)


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_paymentrecord_manual_confirmation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_manual_customer_notice_fields_if_missing,
                    migrations.RunPython.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_amount",
                    field=models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=12,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_transfer_date",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_bank_reference",
                    field=models.CharField(blank=True, max_length=100),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_notes",
                    field=models.TextField(blank=True),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_proof",
                    field=models.FileField(blank=True, null=True, upload_to="payment_proofs/"),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_submitted_by",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manual_payment_notices_submitted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AddField(
                    model_name="paymentrecord",
                    name="manual_customer_submitted_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
            ],
        ),
    ]
