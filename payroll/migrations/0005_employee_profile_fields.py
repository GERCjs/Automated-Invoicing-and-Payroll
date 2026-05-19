from django.db import migrations, models


def ensure_employee_profile_columns(apps, schema_editor):
    table_names = schema_editor.connection.introspection.table_names()
    if "employee" not in table_names and "payroll_employee" in table_names:
        schema_editor.execute("ALTER TABLE payroll_employee RENAME TO employee")

    if "employee" not in schema_editor.connection.introspection.table_names():
        return

    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(),
            "employee",
        )
    }
    columns = [
        ("date_of_birth", "date NULL"),
        ("date_of_appointment", "date NULL"),
        ("legal_status", "varchar(30) NOT NULL DEFAULT ''"),
        ("gender", "varchar(20) NOT NULL DEFAULT ''"),
        ("race", "varchar(60) NOT NULL DEFAULT ''"),
        ("religion", "varchar(60) NOT NULL DEFAULT ''"),
        ("sdl_exempt", "bool NOT NULL DEFAULT 0"),
        ("cpf_exempt", "bool NOT NULL DEFAULT 0"),
        ("job_title", "varchar(150) NOT NULL DEFAULT ''"),
        ("payment_method", "varchar(20) NOT NULL DEFAULT ''"),
        ("bank_name", "varchar(120) NOT NULL DEFAULT ''"),
        ("bank_account_number", "varchar(50) NOT NULL DEFAULT ''"),
        ("bank_branch_code", "varchar(30) NOT NULL DEFAULT ''"),
    ]
    for column_name, column_definition in columns:
        if column_name not in existing_columns:
            schema_editor.execute(f"ALTER TABLE employee ADD COLUMN {column_name} {column_definition}")


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0004_swap_payslip_table_names"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_employee_profile_columns, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterModelTable(
                    name="employee",
                    table="employee",
                ),
                migrations.AddField(
                    model_name="employee",
                    name="bank_account_number",
                    field=models.CharField(blank=True, max_length=50),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="bank_branch_code",
                    field=models.CharField(blank=True, max_length=30),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="bank_name",
                    field=models.CharField(blank=True, max_length=120),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="cpf_exempt",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="date_of_appointment",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="date_of_birth",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="gender",
                    field=models.CharField(
                        blank=True,
                        choices=[("male", "Male"), ("female", "Female"), ("other", "Other")],
                        max_length=20,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="job_title",
                    field=models.CharField(blank=True, max_length=150),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="legal_status",
                    field=models.CharField(
                        blank=True,
                        choices=[
                            ("citizen", "Singapore Citizen"),
                            ("pr", "Permanent Resident"),
                            ("work_permit", "Work Permit"),
                            ("employment_pass", "Employment Pass"),
                            ("s_pass", "S Pass"),
                        ],
                        max_length=30,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="payment_method",
                    field=models.CharField(
                        blank=True,
                        choices=[("cash", "Cash"), ("cheque", "Cheque"), ("giro", "GIRO")],
                        max_length=20,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="race",
                    field=models.CharField(blank=True, max_length=60),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="religion",
                    field=models.CharField(blank=True, max_length=60),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="sdl_exempt",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
