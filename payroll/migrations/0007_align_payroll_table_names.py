from django.db import migrations


def align_payroll_table_names(apps, schema_editor):
    table_names = set(schema_editor.connection.introspection.table_names())
    quote_name = schema_editor.quote_name
    rename_pairs = [
        ("payroll_payrollbatch", "payroll_details"),
        ("payroll_payrollentry", "payroll"),
    ]
    for old_name, new_name in rename_pairs:
        if new_name not in table_names and old_name in table_names:
            schema_editor.execute(
                f"ALTER TABLE {quote_name(old_name)} RENAME TO {quote_name(new_name)}"
            )
            table_names.remove(old_name)
            table_names.add(new_name)


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0006_employee_nric_field"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(align_payroll_table_names, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterModelTable(
                    name="payrollbatch",
                    table="payroll_details",
                ),
                migrations.AlterModelTable(
                    name="payrollentry",
                    table="payroll",
                ),
            ],
        ),
    ]
