from django.db import migrations


MANUAL_CUSTOMER_NOTICE_FIELDS = [
    "manual_customer_amount",
    "manual_customer_transfer_date",
    "manual_customer_bank_reference",
    "manual_customer_notes",
    "manual_customer_proof",
    "manual_customer_submitted_by",
    "manual_customer_submitted_at",
]


def ensure_manual_customer_notice_columns(apps, schema_editor):
    PaymentRecord = apps.get_model("payments", "PaymentRecord")
    table_name = PaymentRecord._meta.db_table
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        existing_tables = set(connection.introspection.table_names(cursor))
        if table_name not in existing_tables:
            return
        existing_columns = {
            column.name
            for column in connection.introspection.get_table_description(cursor, table_name)
        }

    for field_name in MANUAL_CUSTOMER_NOTICE_FIELDS:
        field = PaymentRecord._meta.get_field(field_name)
        if field.column not in existing_columns:
            schema_editor.add_field(PaymentRecord, field)
            existing_columns.add(field.column)


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0006_paymentrecord_manual_customer_amount_and_more"),
    ]

    operations = [
        migrations.RunPython(
            ensure_manual_customer_notice_columns,
            migrations.RunPython.noop,
        ),
    ]
