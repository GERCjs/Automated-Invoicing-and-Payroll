from django.db import migrations, models


def ensure_employee_nric_column(apps, schema_editor):
    if "employee" not in schema_editor.connection.introspection.table_names():
        return
    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(),
            "employee",
        )
    }
    if "nric" not in existing_columns:
        schema_editor.execute("ALTER TABLE employee ADD COLUMN nric varchar(20) NOT NULL DEFAULT ''")


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0005_employee_profile_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_employee_nric_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="employee",
                    name="nric",
                    field=models.CharField(blank=True, max_length=20),
                ),
            ],
        ),
    ]
